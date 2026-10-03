"""Schema walk: tables, columns, keys and row counts from the SQL Server catalog views."""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from typing import Any

from .sql import (
    COLUMNS_QUERY,
    FOREIGN_KEY_QUERY,
    PRIMARY_KEY_QUERY,
    ROW_COUNT_QUERY,
    TABLES_QUERY,
    fetch_dicts,
)


@dataclass(frozen=True, slots=True)
class ColumnInfo:
    name: str
    type_name: str
    max_length: int
    precision: int
    scale: int
    is_nullable: bool
    is_identity: bool


@dataclass(slots=True)
class TableInfo:
    schema: str
    name: str
    object_id: int
    columns: list[ColumnInfo]
    primary_key: list[str]  # declared; empty when the catalog declares none
    row_count: int


@dataclass(slots=True)
class ForeignKey:
    name: str
    child_table: str
    parent_table: str
    child_columns: list[str] = field(default_factory=list)
    parent_columns: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Catalog:
    schema: str
    tables: list[TableInfo]
    foreign_keys: dict[str, ForeignKey]


def _execute(cursor: Any, sql: str, param: Any) -> list[dict[str, Any]]:
    cursor.execute(sql, (param,))
    return fetch_dicts(cursor)


def table_stem(table: str) -> str:
    """A table's name without a leading warehouse prefix (``dimcustomer`` -> ``customer``,
    ``fact_sales`` -> ``sales``); ``dim``/``fact`` elsewhere in a name are left alone."""
    low = table.lower()
    for prefix in ("dim", "fact"):
        if low.startswith(prefix) and len(low) > len(prefix):
            return low[len(prefix) :].removeprefix("_")
    return low


_WORDS = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")


def name_tokens(name: str) -> list[str]:
    """A column name split into lower-case words, at ``_`` and at CamelCase boundaries
    (``customer_id`` and ``CustomerId`` give ``customer, id``; ``customerID`` too;
    ``paid`` is one word)."""
    return [w.lower() for w in _WORDS.findall(name)]


def key_stem(name: str) -> str | None:
    """The words before a trailing ``id`` or ``key`` word (``customer_id`` and ``CustomerKey``
    give ``customer``, ``sales_region_id`` gives ``sales_region``); ``None`` when the name does
    not end in a whole ``id``/``key`` word or has nothing before it. ``paid``, ``valid`` and
    ``monkey`` do not end in one."""
    words = name_tokens(name)
    if len(words) < 2 or words[-1] not in ("id", "key"):
        return None
    return "_".join(words[:-1])


def _squash(name: str) -> str:
    return "".join(name_tokens(name))


def _violates_key(values: list[Any] | None) -> bool:
    """A sample that shows a null or a repeated value rules a column out as a key. No rows
    (``None`` or empty) rule nothing out."""
    if not values:
        return False
    present = [v for v in values if v is not None]
    return len(present) != len(values) or len(set(present)) != len(present)


def guess_primary_key(
    table: str,
    columns: Iterable[ColumnInfo],
    sample: dict[str, list[Any]] | None = None,
    foreign_key_columns: Collection[str] = (),
) -> list[str]:
    """A primary key for a table whose catalog declares none (a Fabric warehouse enforces no
    keys). Only a column that could be the key qualifies: it is not a foreign key (declared,
    shown by the data or suggested by its name) and the sampled rows show no null or repeated
    value in it (with no sampled rows nothing is ruled out). Of those, the identity column wins,
    then a column named ``id`` or like the table: ``<table>_id``, ``<table>_key``, the same with
    the table's singular (``orders`` gives ``order_id``). Otherwise the table is reported with no
    primary key: guessing any other column would only look authoritative."""
    stem = table_stem(table).replace("_", "")
    stems = {stem, stem.removesuffix("s")} - {""}
    wanted = {"id"} | {s + suffix for s in stems for suffix in ("id", "key")}
    blocked = set(foreign_key_columns)
    eligible = [
        c
        for c in columns
        if c.name not in blocked and not _violates_key((sample or {}).get(c.name))
    ]
    for c in eligible:
        if c.is_identity:
            return [c.name]
    for c in eligible:
        if _squash(c.name) in wanted:
            return [c.name]
    return []


def read_catalog(cursor: Any, schema: str, tables: Iterable[str] | None = None) -> Catalog:
    """Walk ``schema``: its tables (optionally only ``tables``), their columns, declared primary
    keys (empty when the catalog has none: :func:`guess_primary_key` judges that from data),
    declared foreign keys and row counts."""
    keep = set(tables) if tables else None
    table_rows = [
        r for r in _execute(cursor, TABLES_QUERY, schema) if keep is None or r["table_name"] in keep
    ]
    counts = {
        r["table_name"]: int(r["row_count"] or 0) for r in _execute(cursor, ROW_COUNT_QUERY, schema)
    }

    fks: dict[str, ForeignKey] = {}
    for r in _execute(cursor, FOREIGN_KEY_QUERY, schema):
        fk = fks.setdefault(
            r["fk_name"], ForeignKey(r["fk_name"], r["child_table"], r["parent_table"])
        )
        fk.child_columns.append(r["child_column"])
        fk.parent_columns.append(r["parent_column"])

    infos: list[TableInfo] = []
    for t in table_rows:
        object_id = t["object_id"]
        columns = [
            ColumnInfo(
                name=c["column_name"],
                # an alias type is read, typed and profiled as its base system type
                type_name=c.get("base_type_name") or c["type_name"],
                max_length=int(c["max_length"] or 0),
                precision=int(c["precision"] or 0),
                scale=int(c["scale"] or 0),
                is_nullable=bool(c["is_nullable"]),
                is_identity=bool(c["is_identity"]),
            )
            for c in _execute(cursor, COLUMNS_QUERY, object_id)
        ]
        pk = [r["column_name"] for r in _execute(cursor, PRIMARY_KEY_QUERY, object_id)]
        infos.append(
            TableInfo(
                schema, t["table_name"], object_id, columns, pk, counts.get(t["table_name"], 0)
            )
        )
    return Catalog(schema, infos, fks)
