"""Schema walk: tables, columns, keys and row counts from the SQL Server catalog views."""

from __future__ import annotations

from collections.abc import Iterable
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
    primary_key: list[str]
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


def guess_primary_key(table: str, columns: Iterable[ColumnInfo]) -> list[str]:
    """A primary key for a table whose catalog declares none (a Fabric warehouse enforces
    no keys): the identity column, else a column named like the table or ``id``, else any
    column ending in ``id`` or ``key``, else the first column."""
    cols = list(columns)
    names = [c.name for c in cols]
    identity = [c.name for c in cols if c.is_identity]
    if identity:
        return [identity[0]]
    stem = table.lower().replace("dim", "").replace("fact", "")
    wanted = (stem + "_id", stem + "id", stem + "_key", stem + "key", "id")
    for name in names:
        if name.lower() in wanted:
            return [name]
    for name in names:
        low = name.lower()
        if low.endswith(("id", "key")):
            return [name]
    return names[:1]


def read_catalog(cursor: Any, schema: str, tables: Iterable[str] | None = None) -> Catalog:
    """Walk ``schema``: its tables (optionally only ``tables``), their columns, primary keys
    (guessed when the catalog has none), foreign keys and row counts."""
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
                type_name=c["type_name"],
                max_length=int(c["max_length"] or 0),
                precision=int(c["precision"] or 0),
                scale=int(c["scale"] or 0),
                is_nullable=bool(c["is_nullable"]),
                is_identity=bool(c["is_identity"]),
            )
            for c in _execute(cursor, COLUMNS_QUERY, object_id)
        ]
        pk = [r["column_name"] for r in _execute(cursor, PRIMARY_KEY_QUERY, object_id)]
        if not pk:
            pk = guess_primary_key(t["table_name"], columns)
        infos.append(
            TableInfo(
                schema, t["table_name"], object_id, columns, pk, counts.get(t["table_name"], 0)
            )
        )
    return Catalog(schema, infos, fks)
