"""Oracle 19c+ Arrow types and lossless bound-value conversions."""

from __future__ import annotations

import datetime as dt
import decimal
import json
from collections.abc import Mapping
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError

PRECISION = {"s": 0, "ms": 3, "us": 6, "ns": 9}
_WIDTHS = {8: 3, 16: 5, 32: 10, 64: 19}


def _width(meta: Mapping[str, Any], column: str) -> int | None:
    value = meta.get("max_length")
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
        raise ShapeError(f"column {column!r}: max_length must be a positive integer")
    return value


def oracle_type(
    field: pa.Field,
    meta: Mapping[str, Any] | None = None,
    *,
    server_major: int = 19,
    max_string_size: int = 4000,
) -> str:
    """The Oracle type holding a declared Arrow field, without observed-value sizing."""
    info = meta or {}
    kind = field.type
    if pa.types.is_dictionary(kind):
        kind = kind.value_type
    width = _width(info, field.name)
    if info.get("type") == "uuid" or (field.metadata or {}).get(b"shape.type") == b"uuid":
        return "VARCHAR2(36 CHAR)"
    if pa.types.is_decimal(kind) or info.get("type") == "decimal":
        p = kind.precision if pa.types.is_decimal(kind) else info.get("precision")
        s = kind.scale if pa.types.is_decimal(kind) else info.get("scale")
        if any(isinstance(v, bool) or not isinstance(v, int) for v in (p, s)):
            raise ShapeError(f"column {field.name!r}: decimal needs precision and scale")
        if not 1 <= p <= 38 or not -84 <= s <= 127:
            raise ShapeError(f"column {field.name!r}: Oracle decimals hold at most 38 digits")
        return f"NUMBER({p},{s})"
    if pa.types.is_boolean(kind):
        return "BOOLEAN" if server_major >= 23 else "NUMBER(1)"
    if pa.types.is_integer(kind):
        digits = _WIDTHS[kind.bit_width]
        if pa.types.is_unsigned_integer(kind):
            digits = 20 if kind.bit_width == 64 else _WIDTHS[kind.bit_width * 2]
        return f"NUMBER({digits})"
    if pa.types.is_floating(kind):
        return "BINARY_DOUBLE" if kind.bit_width == 64 else "BINARY_FLOAT"
    if pa.types.is_string(kind) or pa.types.is_large_string(kind):
        return f"VARCHAR2({width} CHAR)" if width and width <= max_string_size else "CLOB"
    if (
        pa.types.is_binary(kind)
        or pa.types.is_large_binary(kind)
        or pa.types.is_fixed_size_binary(kind)
    ):
        width = kind.byte_width if pa.types.is_fixed_size_binary(kind) else width
        return f"RAW({width})" if width and width <= 2000 else "BLOB"
    if pa.types.is_date(kind):
        return "DATE"
    if pa.types.is_time(kind):
        return f"INTERVAL DAY(0) TO SECOND({PRECISION[kind.unit]})"
    if pa.types.is_timestamp(kind):
        suffix = " WITH TIME ZONE" if kind.tz is not None else ""
        return f"TIMESTAMP({PRECISION[kind.unit]}){suffix}"
    if pa.types.is_nested(kind):
        return "CLOB"
    raise ShapeError(f"column {field.name!r}: Oracle cannot write Arrow type {kind}")


def bind_expression(field: pa.Field, position: int) -> str:
    """Only fixed SQL functions surround a positional value bind."""
    bind = f":{position}"
    if pa.types.is_timestamp(field.type):
        if field.type.tz is not None:
            return f"TO_TIMESTAMP_TZ({bind}, 'YYYY-MM-DD\"T\"HH24:MI:SS.FF9TZH:TZM')"
        return f"TO_TIMESTAMP({bind}, 'YYYY-MM-DD\"T\"HH24:MI:SS.FF9')"
    if pa.types.is_time(field.type):
        return f"TO_DSINTERVAL({bind})"
    return bind


def temporal_text(value: int, kind: pa.DataType) -> str:
    """Format an Arrow temporal integer without passing through Python microseconds."""
    nanos = int(value) * 10 ** (9 - PRECISION[kind.unit])
    seconds, fraction = divmod(nanos, 1_000_000_000)
    if pa.types.is_time(kind):
        if not 0 <= nanos < 86_400_000_000_000:
            raise ShapeError("an Oracle time must be within one day")
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        return f"+0 {hours:02}:{minutes:02}:{seconds:02}.{fraction:09}"
    stamp = dt.datetime(1970, 1, 1) + dt.timedelta(seconds=seconds)
    text = (
        f"{stamp.year:04}-{stamp.month:02}-{stamp.day:02}T"
        f"{stamp.hour:02}:{stamp.minute:02}:{stamp.second:02}.{fraction:09}"
    )
    return text + ("+00:00" if kind.tz is not None else "")


def bound_value(value: Any, field: pa.Field, meta: Mapping[str, Any], server_major: int) -> Any:
    """Normalize one non-temporal value; rows stay separate from SQL text."""
    if value is None:
        return None
    if pa.types.is_nested(field.type):
        return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    if pa.types.is_boolean(field.type) and server_major < 23:
        return int(value)
    if meta.get("type") == "decimal" and not pa.types.is_decimal(field.type):
        value = decimal.Decimal(str(value))
        scale = meta["scale"]
        with decimal.localcontext() as ctx:
            ctx.prec = 100
            if value.quantize(decimal.Decimal(1).scaleb(-scale)) != value:
                raise ShapeError(f"column {field.name!r}: value would lose decimal scale")
    return value
