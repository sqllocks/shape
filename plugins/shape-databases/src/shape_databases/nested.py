"""Nested-shape detection and explicit JSON fallback shared by database sinks.

Native writers keep Arrow buffers. Only the opt-in JSON fallback materializes Python
values, using the same JSON representation as the older SQL text conversion.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
from decimal import Decimal
from typing import Any

import pyarrow as pa
import pyarrow.types as pat

from shape.errors import ShapeError
from shape.io.nested_values import is_nested, validate_json_shape
from shape.io.nested_values import json_batch as json_batch
from shape.io.nested_values import nested_mode as nested_mode


def validate_snowflake_shape(kind: pa.DataType, column: str) -> None:
    """Parquet object transport needs non-empty structs and string-keyed maps."""
    validate_json_shape(kind, column)
    if pat.is_dictionary(kind):
        validate_snowflake_shape(kind.value_type, column)
    elif pat.is_struct(kind):
        if not len(kind):
            raise ShapeError(
                f"column {column!r}: Parquet cannot encode an empty struct; add fields "
                "or encode the value as a JSON string before writing"
            )
        for child in kind:
            validate_snowflake_shape(child.type, column)
    elif pat.is_list(kind) or pat.is_large_list(kind) or pat.is_fixed_size_list(kind):
        validate_snowflake_shape(kind.value_type, column)
    elif pat.is_map(kind):
        if not (pat.is_string(kind.key_type) or pat.is_large_string(kind.key_type)):
            raise ShapeError(
                f"column {column!r}: Snowflake object maps require string keys; "
                "encode the value as a JSON string before writing"
            )
        validate_snowflake_shape(kind.item_type, column)
    elif pat.is_decimal(kind) and (kind.precision > 38 or not 0 <= kind.scale <= kind.precision):
        raise ShapeError(
            f"column {column!r}: Snowflake nested decimals require precision at most 38 "
            "and scale within precision; encode the value as a JSON string before writing"
        )


def validate_object_maps(value: Any, kind: pa.DataType, column: str) -> None:
    """Object-valued map transports cannot preserve duplicate map entries."""
    if value is None:
        return
    if pat.is_dictionary(kind):
        validate_object_maps(value, kind.value_type, column)
    elif pat.is_struct(kind):
        for child in kind:
            validate_object_maps(value[child.name], child.type, column)
    elif pat.is_list(kind) or pat.is_large_list(kind) or pat.is_fixed_size_list(kind):
        for item in value:
            validate_object_maps(item, kind.value_type, column)
    elif pat.is_map(kind):
        keys = [key for key, _ in value]
        if len(keys) != len(set(keys)):
            raise ValueError(
                f"column {column!r}: duplicate map keys cannot become an object; "
                "encode the value as a JSON string before writing"
            )
        for _, item in value:
            validate_object_maps(item, kind.item_type, column)


def _unsupported(field: pa.Field, dialect: str, detail: str = "") -> ShapeError:
    return ShapeError(
        f"column {field.name!r}: nested type {field.type} cannot use the {dialect} "
        f"native transport{': ' + detail if detail else ''}; use nested=json"
    )


def _json_shape(kind: pa.DataType, column: str) -> None:
    """The Python JSON boundary must not silently trim Arrow-only precision."""
    if pat.is_dictionary(kind):
        _json_shape(kind.value_type, column)
    elif pat.is_struct(kind):
        for child in kind:
            _json_shape(child.type, column)
    elif pat.is_list(kind) or pat.is_large_list(kind) or pat.is_fixed_size_list(kind):
        _json_shape(kind.value_type, column)
    elif pat.is_map(kind):
        _json_shape(kind.key_type, column)
        _json_shape(kind.item_type, column)
    elif pat.is_time64(kind) and kind.unit == "ns":
        raise ShapeError(
            f"column {column!r}: nanosecond time cannot cross the Python JSON/array "
            "transport exactly; preencode exact time values as Arrow strings before writing"
        )
    elif not (
        pat.is_null(kind)
        or pat.is_boolean(kind)
        or pat.is_integer(kind)
        or pat.is_floating(kind)
        or pat.is_decimal(kind)
        or pat.is_string(kind)
        or pat.is_large_string(kind)
        or pat.is_binary(kind)
        or pat.is_large_binary(kind)
        or pat.is_fixed_size_binary(kind)
        or pat.is_date(kind)
        or pat.is_time(kind)
        or pat.is_timestamp(kind)
    ):
        raise ShapeError(
            f"column {column!r}: nested leaf {kind} cannot use native JSON; use nested=json"
        )


def native_type(field: pa.Field, dialect: str) -> str:
    """Resolve a native container's SQL type; scalar writers keep their existing maps."""
    from . import _sql

    kind = field.type
    if pat.is_dictionary(kind):
        return native_type(field.with_type(kind.value_type), dialect)
    validate_json_shape(kind, field.name)
    lists = pat.is_list(kind) or pat.is_large_list(kind) or pat.is_fixed_size_list(kind)
    if dialect == "mysql" and is_nested(kind):
        _json_shape(kind, field.name)
        return "JSON"
    if dialect == "postgres":
        if pat.is_struct(kind) or pat.is_map(kind):
            _json_shape(kind, field.name)
            return "JSONB"
        if lists:
            if pat.is_struct(kind.value_type):
                _json_shape(kind, field.name)
                return "JSONB"
            if is_nested(kind.value_type):
                raise _unsupported(field, dialect, "arrays require scalar elements")
            return native_type(kind.value_field.with_name(field.name), dialect) + "[]"
    if dialect == "databricks":
        if lists:
            return "ARRAY<" + native_type(kind.value_field.with_name(field.name), dialect) + ">"
        if pat.is_struct(kind):
            names = [child.name.lower() for child in kind]
            if not names or len(names) != len(set(names)):
                raise _unsupported(field, dialect, "struct fields must be non-empty and distinct")
            parts = []
            for child in kind:
                try:
                    _sql.check_identifier(child.name, "column", dialect)
                except ShapeError:
                    raise _unsupported(field, dialect, "unsupported struct field name") from None
                parts.append(
                    f"{_sql.quote(child.name, dialect)}: "
                    + native_type(child.with_name(field.name), dialect)
                )
            return "STRUCT<" + ", ".join(parts) + ">"
        if pat.is_map(kind):
            if not (pat.is_string(kind.key_type) or pat.is_large_string(kind.key_type)):
                raise _unsupported(field, dialect, "from_json requires STRING map keys")
            return (
                "MAP<STRING, " + native_type(kind.item_field.with_name(field.name), dialect) + ">"
            )
    if pat.is_boolean(kind):
        return "BOOLEAN"
    if pat.is_integer(kind):
        width = kind.bit_width + (1 if pat.is_unsigned_integer(kind) else 0)
        if width > 64:
            return "NUMERIC(20,0)" if dialect == "postgres" else "DECIMAL(20,0)"
        if dialect == "postgres":
            return "SMALLINT" if width <= 16 else "INTEGER" if width <= 32 else "BIGINT"
        return (
            "TINYINT"
            if width <= 8
            else "SMALLINT"
            if width <= 16
            else "INT"
            if width <= 32
            else "BIGINT"
        )
    if pat.is_floating(kind):
        if dialect == "postgres":
            return "DOUBLE PRECISION" if kind.bit_width == 64 else "REAL"
        return "DOUBLE" if kind.bit_width == 64 else "FLOAT"
    if pat.is_decimal(kind):
        if dialect == "databricks" and (
            kind.precision > 38 or not 0 <= kind.scale <= kind.precision
        ):
            raise _unsupported(field, dialect, "decimal precision/scale is unsupported")
        prefix = "NUMERIC" if dialect == "postgres" else "DECIMAL"
        return f"{prefix}({kind.precision},{kind.scale})"
    if pat.is_string(kind) or pat.is_large_string(kind):
        return "TEXT" if dialect == "postgres" else "STRING"
    if pat.is_binary(kind) or pat.is_large_binary(kind) or pat.is_fixed_size_binary(kind):
        return "BYTEA" if dialect == "postgres" else "BINARY"
    if pat.is_date(kind):
        return "DATE"
    if pat.is_timestamp(kind):
        if dialect == "postgres":
            return "TIMESTAMPTZ" if kind.tz else "TIMESTAMP"
        return "TIMESTAMP" if kind.tz else "TIMESTAMP_NTZ"
    if pat.is_time(kind) and dialect == "postgres":
        _json_shape(kind, field.name)
        return "TIME(6)"
    raise _unsupported(field, dialect)


def _json_value(value: Any, kind: pa.DataType, dialect: str, column: str) -> Any:
    if value is None:
        return None
    if pat.is_dictionary(kind):
        return _json_value(value, kind.value_type, dialect, column)
    if pat.is_struct(kind):
        return {
            child.name: _json_value(value[child.name], child.type, dialect, column)
            for child in kind
        }
    if pat.is_list(kind) or pat.is_large_list(kind) or pat.is_fixed_size_list(kind):
        return [_json_value(item, kind.value_type, dialect, column) for item in value]
    if pat.is_map(kind):
        pairs = [
            [
                _json_value(key, kind.key_type, dialect, column),
                _json_value(item, kind.item_type, dialect, column),
            ]
            for key, item in value
        ]
        if dialect == "databricks":
            names = [key for key, _ in pairs]
            if len(names) != len(set(names)):
                raise ValueError(
                    f"column {column!r}: duplicate map keys cannot use from_json; use nested=json"
                )
            return dict(pairs)
        return pairs
    if pat.is_timestamp(kind) and dialect == "databricks" and kind.unit == "ns":
        if pa.scalar(value, type=kind).value % 1000:
            raise ValueError(
                f"column {column!r}: Databricks timestamp precision is microseconds; "
                "use nested=json"
            )
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(value).decode("ascii")
    return value


def native_json(value: Any, field: pa.Field, dialect: str) -> str | None:
    """JSON data with lossless decimal/temporal text and binary base64 encoding."""
    if dialect in ("postgres", "mysql"):
        _json_shape(field.type, field.name)
    if value is None:
        return None
    encoded = _json_value(value, field.type, dialect, field.name)
    try:
        return json.dumps(encoded, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (ValueError, TypeError):
        raise ValueError(
            f"column {field.name!r}: nested JSON requires finite numbers and supported "
            "JSON scalars; use nested=json"
        ) from None


def postgres_array(value: Any) -> str | None:
    """One text-format PostgreSQL array literal, passed as COPY data, never SQL."""
    if value is None:
        return None
    elements = []
    for item in value:
        if item is None:
            elements.append("NULL")
            continue
        if isinstance(item, (bytes, bytearray, memoryview)):
            text = "\\x" + bytes(item).hex()
        elif isinstance(item, (dt.datetime, dt.date, dt.time)):
            text = item.isoformat()
        else:
            text = str(item)
        elements.append('"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"')
    return "{" + ",".join(elements) + "}"
