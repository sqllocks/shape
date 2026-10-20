"""Arrow readers for Snowflake accounts and Databricks SQL warehouses."""

from __future__ import annotations

import re
from typing import Any

import pyarrow as pa


def cloud_arrow_type(
    dialect: str, type_name: str, precision: int | None = None, scale: int | None = None
) -> Any:
    """Translate catalog types, preserving numeric width, scale and timestamp zones.

    Nested values cross this source boundary as JSON text. A Snowflake NUMBER(p,0)
    uses the smallest signed Arrow integer containing every p-digit signed value;
    19 through 38 digits need decimal128.
    """
    kind = type_name.upper().strip()
    if match := re.fullmatch(r"(?:DECIMAL|NUMBER|NUMERIC)\((\d+),\s*(\d+)\)", kind):
        precision, scale = int(match[1]), int(match[2])
        kind = "DECIMAL"
    if kind in {"NUMBER", "DECIMAL", "NUMERIC", "FIXED"}:
        p = 38 if precision is None else precision
        s = 0 if scale is None else scale
        if not 1 <= p <= 38 or not 0 <= s <= p:
            raise ValueError("catalog decimal requires precision 1..38 and scale 0..precision")
        if dialect == "snowflake" and s == 0:
            for digits, dtype in (
                (2, pa.int8()),
                (4, pa.int16()),
                (9, pa.int32()),
                (18, pa.int64()),
            ):
                if p <= digits:
                    return dtype
        return pa.decimal128(p, s)
    integers = {
        "TINYINT": pa.int8(),
        "SMALLINT": pa.int16(),
        "INT": pa.int32(),
        "INTEGER": pa.int32(),
        "BIGINT": pa.int64(),
    }
    if kind in integers:
        return integers[kind]
    if kind in {"FLOAT", "DOUBLE", "DOUBLE PRECISION", "REAL"}:
        return pa.float64() if dialect == "snowflake" or kind != "FLOAT" else pa.float32()
    if kind in {"TIMESTAMP_NTZ", "TIMESTAMP WITHOUT TIME ZONE"}:
        return pa.timestamp("us")
    if kind in {"TIMESTAMP", "TIMESTAMP_TZ", "TIMESTAMP_LTZ"}:
        return pa.timestamp("us", "UTC")
    if kind == "DATE":
        return pa.date32()
    if kind == "TIME":
        return pa.time64("us")
    if kind in {"BOOLEAN", "BOOL"}:
        return pa.bool_()
    if kind in {"BINARY", "VARBINARY"}:
        return pa.binary()
    return pa.string()
