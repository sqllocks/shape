"""SQL text and value preparation shared by the PostgreSQL and MySQL sinks.

Identifier quoting and the Arrow -> database type map are core's: the ``sql`` sink's script
dialects ``postgres`` and ``mysql`` (:mod:`shape.builtins.sinks.sql`), so a table created by a
live write has the same columns as the table the script would create. This module adds what a
live write needs on top: strict identifier checks (a database truncates or rejects what a script
file would not notice), the ``CREATE TABLE`` text, and per-column value conversion.

Values are never part of a statement: they go through ``COPY ... FROM STDIN`` (PostgreSQL) or
bound ``executemany`` parameters (MySQL). Everything that is interpolated is an identifier that
passed :func:`check_identifier` and then core's quoting.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]
import pyarrow.types as pat  # type: ignore[import-untyped]

from shape.builtins.sinks import sql as core_sql
from shape.errors import ShapeError

WRITE_MODES = ("create", "append", "truncate", "replace")
DEFAULT_BATCH_SIZE = 5000
# PostgreSQL limits an identifier to 63 bytes and silently truncates longer names; MySQL to 64
# characters. A write fails instead of creating a table under a different name.
MAX_IDENTIFIER = {"postgres": 63, "mysql": 64, "snowflake": 255, "databricks": 255}
# Quoting is core's for the two classic dialects; Snowflake quotes like PostgreSQL and Databricks
# like MySQL (backticks).
_QUOTE_AS = {"snowflake": "postgres", "databricks": "mysql"}
# Characters Delta refuses in a column name (without column mapping), and those Unity Catalog
# refuses in a table or schema name.
_DELTA_COLUMN_BAD = frozenset(" ,;{}()\n\t=")
_UC_NAME_BAD = frozenset(" ./`")
# A string column longer than this becomes TEXT / LONGTEXT instead of VARCHAR(n).
MAX_VARCHAR = 4000
_TEXT = {"postgres": "TEXT", "mysql": "LONGTEXT"}


def check_identifier(name: Any, kind: str, dialect: str) -> str:
    """``name`` when it is safe to quote and the database keeps it as is, else a ShapeError."""
    if not isinstance(name, str) or not name:
        raise ShapeError(f"{kind} name must be a non-empty string")
    if "\x00" in name:
        raise ShapeError(f"{kind} name {name!r} contains a NUL character")
    limit = MAX_IDENTIFIER[dialect]
    size = len(name.encode("utf-8")) if dialect == "postgres" else len(name)
    if size > limit:
        raise ShapeError(
            f"{kind} name {name[:30]!r}... is longer than the {limit} {dialect} allows"
        )
    if dialect in ("snowflake", "databricks"):
        _check_cloud_identifier(name, kind, dialect)
    if dialect == "mysql":
        if "%" in name:
            # PyMySQL formats statements with %; a % in a name would be read as a placeholder.
            raise ShapeError(f"{kind} name {name!r}: '%' is not allowed in a MySQL name")
        if name != name.rstrip(" "):
            raise ShapeError(f"{kind} name {name!r}: MySQL names cannot end with a space")
    return name


def _check_cloud_identifier(name: str, kind: str, dialect: str) -> None:
    """What Snowflake and Databricks would change or refuse, said before any connection."""
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ShapeError(f"{kind} name {name!r} contains a control character")
    if dialect == "snowflake":
        if name != name.strip():
            raise ShapeError(
                f"{kind} name {name!r}: Snowflake names cannot start or end with a space"
            )
        return
    if kind == "column":
        bad = sorted(c for c in name if c in _DELTA_COLUMN_BAD)
        if bad:
            raise ShapeError(
                f"column name {name!r} contains {bad[0]!r}, which a Delta column name cannot hold"
            )
        return
    # a table or schema: Unity Catalog stores these in lower case, so a name with capitals would
    # be created under a different name than the one asked for
    if any(c in _UC_NAME_BAD for c in name):
        raise ShapeError(
            f"{kind} name {name!r}: Unity Catalog names cannot contain a space, '.', '/' or '`'"
        )
    if name != name.lower():
        raise ShapeError(
            f"{kind} name {name!r}: Unity Catalog stores {kind} names in lower case; "
            f"use {name.lower()!r}"
        )


def check_unique(names: Sequence[str], dialect: str) -> None:
    """Column names that the database would take for one name are refused."""
    seen: set[str] = set()
    for name in names:
        key = name.lower() if dialect == "databricks" else name
        if key in seen:
            raise ShapeError(f"column name {name!r} appears twice (or differs only in case)")
        seen.add(key)


def quote(name: str, dialect: str) -> str:
    """One quoted identifier (core's quoting: ``"x"`` for PostgreSQL and Snowflake, backticks for
    MySQL and Databricks)."""
    return str(core_sql._quote(name, _QUOTE_AS.get(dialect, dialect)))


def qualified(schema_name: str | None, table: str, dialect: str) -> str:
    return (f"{quote(schema_name, dialect)}." if schema_name else "") + quote(table, dialect)


def check_mode(mode: Any) -> str:
    if mode not in WRITE_MODES:
        raise ShapeError(f"unknown write mode {mode!r}; choose one of {', '.join(WRITE_MODES)}")
    return str(mode)


def positive_int(value: Any, name: str, *, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ShapeError(f"{name} must be a positive integer")
    return value


def _observed_length(column: pa.ChunkedArray | pa.Array) -> int:
    if not (pat.is_string(column.type) or pat.is_large_string(column.type)):
        return -1
    longest = pc.max(pc.utf8_length(column)).as_py()
    return int(longest or 0)


def column_definitions(
    schema: pa.Schema,
    first: pa.RecordBatch | None,
    meta: Mapping[str, Mapping[str, Any]],
    key: Sequence[str],
    dialect: str,
) -> list[tuple[str, str, bool]]:
    """``(name, sql type, nullable)`` per column: core's type map, with string columns wide
    enough for the first batch (a later, longer value fails the write rather than truncating)."""
    out: list[tuple[str, str, bool]] = []
    for index, field in enumerate(schema):
        info: dict[str, Any] = dict(meta.get(field.name, {}))
        sql_type: str | None = None
        if core_sql._logical_type(field.type) == "string" and not info.get("max_length"):
            seen = _observed_length(first.column(index)) if first is not None else 0
            if not (pat.is_string(field.type) or pat.is_large_string(field.type)):
                seen = -1
            if seen < 0 or seen > MAX_VARCHAR:
                sql_type = _TEXT[dialect]  # nested, dictionary or very long text
            else:
                info["max_length"] = max(255, seen)
        if sql_type is None:
            sql_type = core_sql._column_type(field, info, dialect)
        nullable = bool(info.get("nullable", True)) and field.name not in key
        out.append((field.name, sql_type, nullable))
    return out


def create_table_sql(
    schema_name: str | None,
    table: str,
    schema: pa.Schema,
    dialect: str,
    *,
    first: pa.RecordBatch | None = None,
    columns: Mapping[str, Mapping[str, Any]] | None = None,
    primary_key: Sequence[str] = (),
) -> str:
    for name in schema.names:
        check_identifier(name, "column", dialect)
    for name in primary_key:
        if name not in schema.names:
            raise ShapeError(f"primary key column {name!r} is not a column of {table!r}")
    defs = [
        f"  {quote(name, dialect)} {sql_type} {'NULL' if nullable else 'NOT NULL'}"
        for name, sql_type, nullable in column_definitions(
            schema, first, columns or {}, primary_key, dialect
        )
    ]
    if primary_key:
        defs.append(f"  PRIMARY KEY ({', '.join(quote(c, dialect) for c in primary_key)})")
    body = ",\n".join(defs)
    return f"CREATE TABLE {qualified(schema_name, table, dialect)} (\n{body}\n)"


def drop_table_sql(schema_name: str | None, table: str, dialect: str) -> str:
    suffix = " CASCADE" if dialect == "postgres" else ""
    return f"DROP TABLE IF EXISTS {qualified(schema_name, table, dialect)}{suffix}"


def truncate_sql(schema_name: str | None, table: str, dialect: str) -> str:
    return f"TRUNCATE TABLE {qualified(schema_name, table, dialect)}"


def check_batch(first: pa.Schema, batch: pa.RecordBatch, table: str) -> None:
    if list(batch.schema.names) != list(first.names):
        raise ShapeError(
            f"a batch for {table!r} has columns {list(batch.schema.names)} but the first one "
            f"had {list(first.names)}"
        )


# -- values --------------------------------------------------------------------------------
def _utc_naive(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(dt.UTC).replace(tzinfo=None)


def _json(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


def _converter(field: pa.Field, dialect: str) -> Callable[[Any], Any] | None:
    """The per-value fix a column needs before it reaches the driver, or ``None``.

    Timestamps lose their zone as UTC (the column type has none, as in the script dialect);
    nested values become JSON text; durations become text; MySQL cannot store NaN or infinity,
    so they become NULL (as in the script dialect)."""
    kind = field.type
    if pat.is_timestamp(kind) and kind.tz is not None:
        return lambda v: None if v is None else _utc_naive(v)
    if pat.is_list(kind) or pat.is_large_list(kind) or pat.is_struct(kind) or pat.is_map(kind):
        return lambda v: None if v is None else _json(v)
    if pat.is_duration(kind):
        return lambda v: None if v is None else str(v)
    if dialect == "mysql" and pat.is_floating(kind):
        return lambda v: None if v is None or not math.isfinite(v) else v
    return None


def row_iterator(
    batch: pa.RecordBatch, dialect: str, converters: Sequence[Callable[[Any], Any] | None]
) -> Iterator[tuple[Any, ...]]:
    columns = []
    for index in range(batch.num_columns):
        values = batch.column(index).to_pylist()
        convert = converters[index]
        columns.append(values if convert is None else [convert(v) for v in values])
    return zip(*columns, strict=True)


def converters_for(schema: pa.Schema, dialect: str) -> list[Callable[[Any], Any] | None]:
    return [_converter(field, dialect) for field in schema]


def normalize(batch: pa.RecordBatch) -> pa.RecordBatch:
    """Dictionary columns decoded, so every value reaches the driver as its plain value."""
    if not any(pat.is_dictionary(f.type) for f in batch.schema):
        return batch
    arrays = [c.dictionary_decode() if pat.is_dictionary(c.type) else c for c in batch.columns]
    return pa.RecordBatch.from_arrays(arrays, names=batch.schema.names)
