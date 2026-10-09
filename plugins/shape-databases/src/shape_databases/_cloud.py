"""What the Snowflake and Databricks sinks share: the Arrow type maps, the checks made before
any connection, and the Parquet staging used by Snowflake.

Types are mapped here, not by core's script dialects, because a live write has to say exactly
what each Arrow type becomes (see the plugin README). A type with no mapping is refused before a
connection is opened, with the column's name in the message.

Nothing in this module builds a statement from a value: every name is checked
(:func:`shape_databases._sql.check_identifier`) and quoted, and a size, precision or scale is
an integer that has been checked.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]
import pyarrow.types as pat  # type: ignore[import-untyped]

from shape.builtins.sinks.sql import _column_type
from shape.errors import ShapeError

from . import _sql

DEFAULT_CHUNK_ROWS = 1_000_000
MAX_DECIMAL_PRECISION = 38
SNOWFLAKE_MAX_VARCHAR = 16_777_216


def is_nested(t: pa.DataType) -> bool:
    return bool(
        pat.is_list(t)
        or pat.is_large_list(t)
        or pat.is_fixed_size_list(t)
        or pat.is_struct(t)
        or pat.is_map(t)
    )


def _value_type(t: pa.DataType) -> pa.DataType:
    return t.value_type if pat.is_dictionary(t) else t


def _unsupported(field: pa.Field, dialect: str) -> ShapeError:
    return ShapeError(
        f"column {field.name!r} has type {field.type}, which the {dialect} sink cannot store: "
        "convert it (for example to a string) before writing"
    )


def _decimal(field: pa.Field, t: pa.DataType) -> str:
    if t.scale < 0 or t.scale > t.precision:
        raise ShapeError(f"column {field.name!r}: decimal scale must be between 0 and precision")
    if t.precision > MAX_DECIMAL_PRECISION:
        raise ShapeError(
            f"column {field.name!r}: a decimal holds at most {MAX_DECIMAL_PRECISION} digits"
        )
    return f"({t.precision},{t.scale})"


def snowflake_type(field: pa.Field, meta: Mapping[str, Any] | None = None) -> str:
    """The Snowflake type that holds ``field``."""
    t = _value_type(field.type)
    if (meta or {}).get("type") == "uuid" or (field.metadata or {}).get(b"type") == b"uuid":
        return "VARCHAR(36)"
    if (meta or {}).get("type") == "decimal" and not pat.is_decimal(t):
        return _column_type(field, meta or {}, "tsql").replace("DECIMAL", "NUMBER")
    if pat.is_boolean(t):
        return "BOOLEAN"
    if pat.is_integer(t):
        bits = t.bit_width
        if pat.is_unsigned_integer(t):
            if bits == 64:
                return "DECIMAL(20,0)"
            bits *= 2
        digits = {8: 3, 16: 5, 32: 10, 64: 19}[bits]
        return f"NUMBER({digits},0)"
    if pat.is_floating(t):
        return "FLOAT(24)" if t.bit_width <= 32 else "FLOAT"
    if pat.is_decimal(t):
        return "NUMBER" + _decimal(field, t)
    if pat.is_string(t) or pat.is_large_string(t):
        declared = (meta or {}).get("max_length")
        if declared is not None:
            size = declared
            if (
                isinstance(size, bool)
                or not isinstance(size, int)
                or not 1 <= size <= SNOWFLAKE_MAX_VARCHAR
            ):
                raise ShapeError(
                    f"column {field.name!r}: max_length must be between 1 and "
                    f"{SNOWFLAKE_MAX_VARCHAR}"
                )
            return f"VARCHAR({size})"
        return "VARCHAR"
    if pat.is_binary(t) or pat.is_large_binary(t) or pat.is_fixed_size_binary(t):
        return "BINARY"
    if pat.is_date(t):
        return "DATE"
    if pat.is_timestamp(t):
        prefix = "TIMESTAMP_NTZ" if t.tz is None else "TIMESTAMP_TZ"
        precision = {"s": 0, "ms": 3, "us": 6, "ns": 9}[t.unit]
        return f"{prefix}({precision})"
    if pat.is_time(t):
        precision = {"s": 0, "ms": 3, "us": 6, "ns": 9}[t.unit]
        return f"TIME({precision})"
    if is_nested(t):
        return "VARIANT"
    raise _unsupported(field, "snowflake")


def databricks_type(field: pa.Field, meta: Mapping[str, Any] | None = None) -> str:
    """The Databricks (Delta) type that holds ``field``."""
    t = _value_type(field.type)
    if (meta or {}).get("type") == "uuid" or (field.metadata or {}).get(b"type") == b"uuid":
        return "VARCHAR(36)"
    if (meta or {}).get("type") == "decimal" and not pat.is_decimal(t):
        return _column_type(field, meta or {}, "tsql")
    if pat.is_boolean(t):
        return "BOOLEAN"
    if pat.is_integer(t):
        if pat.is_uint64(t):
            return "DECIMAL(20,0)"
        bits = t.bit_width * (2 if pat.is_unsigned_integer(t) else 1)
        return {8: "TINYINT", 16: "SMALLINT", 32: "INT", 64: "BIGINT"}[bits]
    if pat.is_floating(t):
        return "FLOAT" if t.bit_width <= 32 else "DOUBLE"
    if pat.is_decimal(t):
        return "DECIMAL" + _decimal(field, t)
    if pat.is_string(t) or pat.is_large_string(t) or is_nested(t):
        if (pat.is_string(t) or pat.is_large_string(t)) and (meta or {}).get(
            "max_length"
        ) is not None:
            return _column_type(field, meta or {}, "mysql")
        return "STRING"  # lists, structs and maps are JSON text
    if pat.is_binary(t) or pat.is_large_binary(t) or pat.is_fixed_size_binary(t):
        return "BINARY"
    if pat.is_date(t):
        return "DATE"
    if pat.is_timestamp(t):
        return "TIMESTAMP_NTZ" if t.tz is None else "TIMESTAMP"
    if pat.is_time(t):
        return "TIME(6)"
    raise _unsupported(field, "databricks")


def check_types(
    schema: pa.Schema,
    dialect: str,
    columns: Mapping[str, Mapping[str, Any]] | None = None,
    primary_key: Sequence[str] = (),
) -> None:
    """Refuse, before any connection, a column with no type, a bad ``columns`` entry or a key
    that is not a column."""
    for name in primary_key:
        if name not in schema.names:
            raise ShapeError(f"primary key column {name!r} is not a column of the table")
    for field in schema:
        if dialect == "snowflake":
            snowflake_type(field, (columns or {}).get(field.name))
        else:
            databricks_type(field, (columns or {}).get(field.name))


def create_table_sql(
    qualified_name: str,
    schema: pa.Schema,
    dialect: str,
    columns: Mapping[str, Mapping[str, Any]],
    primary_key: Sequence[str],
) -> str:
    """``CREATE TABLE`` with the mapped types; key columns are ``NOT NULL`` and the key is
    declared (neither database enforces it)."""
    for name in primary_key:
        if name not in schema.names:
            raise ShapeError(f"primary key column {name!r} is not a column of the table")
    lines = []
    for field in schema:
        meta = columns.get(field.name, {})
        sql_type = (
            snowflake_type(field, meta)
            if dialect == "snowflake"
            else databricks_type(field, (columns or {}).get(field.name))
        )
        nullable = bool(meta.get("nullable", True)) and field.name not in primary_key
        lines.append(
            f"  {_sql.quote(field.name, dialect)} {sql_type}{'' if nullable else ' NOT NULL'}"
        )
    if primary_key:
        lines.append(f"  PRIMARY KEY ({', '.join(_sql.quote(c, dialect) for c in primary_key)})")
    body = ",\n".join(lines)
    tail = " USING DELTA" if dialect == "databricks" else ""
    return f"CREATE TABLE {qualified_name} (\n{body}\n){tail}"  # nosec B608


# -- Parquet staging ----------------------------------------------------------------------


def parquet_batch(batch: pa.RecordBatch) -> pa.RecordBatch:
    """``batch`` as it is staged: dictionaries decoded, timestamps in microseconds (a zone is
    converted to UTC first)."""
    arrays = []
    for col in batch.columns:
        t = col.type
        if pat.is_dictionary(t):
            col = col.cast(t.value_type)
            t = col.type
        if pat.is_timestamp(t):
            if t.tz is not None:
                col = col.cast(pa.timestamp(t.unit, "UTC"))
            col = col.cast(pa.timestamp("us", t.tz and "UTC"), safe=False)
        elif pat.is_floating(t) and pat.is_float16(t):
            col = pc.cast(col, pa.float32())
        arrays.append(col)
    return pa.RecordBatch.from_arrays(arrays, names=batch.schema.names)


def chunked(batches: Iterable[pa.RecordBatch], chunk_rows: int) -> Iterator[list[pa.RecordBatch]]:
    """``batches`` regrouped into lists of at most ``chunk_rows`` rows (batches are sliced)."""
    group: list[pa.RecordBatch] = []
    size = 0
    for batch in batches:
        offset = 0
        while offset < batch.num_rows:
            take = min(chunk_rows - size, batch.num_rows - offset)
            group.append(batch.slice(offset, take))
            size += take
            offset += take
            if size >= chunk_rows:
                yield group
                group, size = [], 0
    if group:
        yield group


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def string_literal(text: str) -> str:
    """``text`` as a single-quoted SQL literal (backslash and quote escaped). Used only for
    file paths and patterns that this package generated, which cannot be parameters."""
    return "'" + text.replace("\\", "\\\\").replace("'", "''") + "'"
