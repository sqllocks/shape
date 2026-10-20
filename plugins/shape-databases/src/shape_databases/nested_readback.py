"""Schema-guided nested read-back; no profiling or child-schema inference.

JSON-backed catalogs do not retain Arrow child types. Callers must supply the original
field to restore values. Native Databricks types instead use their full catalog schema.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import json
import math
import re
from decimal import Decimal, InvalidOperation
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.types as pat  # type: ignore[import-untyped]

from shape.io.nested_values import validate_json_shape


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object field")
        result[key] = value
    return result


def _constant(value: str) -> Any:
    raise ValueError("non-finite JSON number")


def _value(value: Any, field: pa.Field) -> Any:
    result: Any
    kind = field.type
    if value is None:
        if not field.nullable:
            raise ValueError("null in required field")
        return None
    if pat.is_dictionary(kind):
        return _value(value, field.with_type(kind.value_type))
    if pat.is_struct(kind):
        if not isinstance(value, dict) or set(value) != set(kind.names):
            raise ValueError("struct fields do not match the original schema")
        return {child.name: _value(value[child.name], child) for child in kind}
    if pat.is_list(kind) or pat.is_large_list(kind) or pat.is_fixed_size_list(kind):
        if not isinstance(value, list):
            raise ValueError("expected JSON array")
        if pat.is_fixed_size_list(kind) and len(value) != kind.list_size:
            raise ValueError("fixed-size list length differs")
        return [_value(item, kind.value_field) for item in value]
    if pat.is_map(kind):
        if isinstance(value, dict):
            if not (pat.is_string(kind.key_type) or pat.is_large_string(kind.key_type)):
                raise ValueError("object map requires string keys")
            pairs = list(value.items())
        elif isinstance(value, list):
            pairs = value
        else:
            raise ValueError("expected object or key/value pairs")
        result = []
        for pair in pairs:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ValueError("map entry must be a key/value pair")
            result.append((_value(pair[0], kind.key_field), _value(pair[1], kind.item_field)))
        return result
    if pat.is_boolean(kind):
        if type(value) is not bool:
            raise ValueError("expected boolean")
        return value
    if pat.is_integer(kind):
        if type(value) is not int:
            raise ValueError("expected integer")
        return value
    if pat.is_floating(kind):
        if isinstance(value, bool) or not isinstance(value, (int, Decimal, float)):
            raise ValueError("expected number")
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("non-finite number")
        return result
    if pat.is_decimal(kind):
        if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
            raise ValueError("expected decimal text or exact JSON number")
        result = Decimal(value)
        if not result.is_finite():
            raise ValueError("non-finite decimal")
        return result
    if pat.is_string(kind) or pat.is_large_string(kind):
        if not isinstance(value, str):
            raise ValueError("expected string")
        return value
    if pat.is_binary(kind) or pat.is_large_binary(kind) or pat.is_fixed_size_binary(kind):
        if not isinstance(value, str):
            raise ValueError("expected base64 text")
        return base64.b64decode(value, validate=True)
    if pat.is_timestamp(kind):
        if not isinstance(value, str):
            raise ValueError("expected ISO timestamp")
        result = dt.datetime.fromisoformat(value)
        if (result.tzinfo is None) != (kind.tz is None):
            raise ValueError("timestamp zone differs from original schema")
        fraction = re.search(r"\d{2}:\d{2}:\d{2}\.(\d+)", value)
        digits = {"s": 0, "ms": 3, "us": 6, "ns": 9}[kind.unit]
        if fraction and any(char != "0" for char in fraction[1][digits:]):
            raise ValueError("timestamp precision exceeds the original schema")
        return pa.scalar(value).cast(kind)
    if pat.is_date(kind):
        if not isinstance(value, str):
            raise ValueError("expected ISO date")
        return dt.date.fromisoformat(value)
    if pat.is_time(kind):
        if not isinstance(value, str):
            raise ValueError("expected ISO time")
        match = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?", value)
        if match is None:
            raise ValueError("expected ISO time without zone")
        hour, minute, second = map(int, match.groups()[:3])
        dt.time(hour, minute, second)  # validate clock bounds
        nanos = ((hour * 60 + minute) * 60 + second) * 1_000_000_000
        nanos += int((match[4] or "").ljust(9, "0"))
        divisor = {"s": 1_000_000_000, "ms": 1_000_000, "us": 1000, "ns": 1}[kind.unit]
        if nanos % divisor:
            raise ValueError("time precision exceeds the original schema")
        return pa.scalar(nanos // divisor, type=kind)
    raise ValueError("unsupported original Arrow type")


def restore_json_column(values: Any, field: pa.Field) -> pa.Array:
    """Decode source JSON once and rebuild the caller's original Arrow field.

    This is for JSON-backed physical columns, never a replacement for native source type
    checks. SQL NULL and JSON null become Arrow null. Object duplicates and coercions are
    refused. Map key/value arrays retain duplicate entries where the target retained them.
    """
    validate_json_shape(field.type, field.name)
    try:
        decoded = []
        for raw in values:
            if raw is not None and not isinstance(raw, str):
                raise ValueError("source must return JSON text or SQL NULL")
            value = (
                None
                if raw is None
                else json.loads(
                    raw, object_pairs_hook=_object, parse_float=Decimal, parse_constant=_constant
                )
            )
            decoded.append(_value(value, field))
        return pa.array(decoded, type=field.type)
    except (
        ValueError,
        TypeError,
        OverflowError,
        InvalidOperation,
        binascii.Error,
        pa.ArrowException,
    ) as exc:
        raise ValueError(
            f"column {field.name!r}: source JSON does not match the original Arrow schema: {exc}"
        ) from None


class _Catalog:
    def __init__(self, value: str) -> None:
        self.value = value
        self.position = 0

    def space(self) -> None:
        while self.position < len(self.value) and self.value[self.position].isspace():
            self.position += 1

    def take(self, text: str) -> bool:
        self.space()
        if self.value.startswith(text, self.position):
            self.position += len(text)
            return True
        return False

    def need(self, text: str) -> None:
        if not self.take(text):
            raise ValueError(f"expected {text}")

    def word(self) -> str:
        self.space()
        match = re.match(r"[A-Za-z_][A-Za-z_0-9]*", self.value[self.position :])
        if match is None:
            raise ValueError("expected type or field name")
        self.position += len(match[0])
        return match[0]

    def name(self) -> str:
        if not self.take("`"):
            return self.word()
        result = ""
        while self.position < len(self.value):
            char = self.value[self.position]
            self.position += 1
            if char == "`":
                if self.position < len(self.value) and self.value[self.position] == "`":
                    self.position += 1
                    result += "`"
                else:
                    return result
            else:
                result += char
        raise ValueError("unterminated quoted field")

    def kind(self) -> pa.DataType:
        word = self.word().upper()
        if word == "ARRAY":
            self.need("<")
            result = pa.list_(self.kind())
            self.need(">")
            return result
        if word == "MAP":
            self.need("<")
            key = self.kind()
            if key != pa.string():
                raise ValueError("from_json map catalog requires STRING keys")
            self.need(",")
            result = pa.map_(key, self.kind())
            self.need(">")
            return result
        if word == "STRUCT":
            self.need("<")
            fields = []
            while True:
                name = self.name()
                self.need(":")
                fields.append(pa.field(name, self.kind()))
                if not self.take(","):
                    break
            self.need(">")
            names = [field.name.lower() for field in fields]
            if len(names) != len(set(names)):
                raise ValueError("duplicate struct fields")
            return pa.struct(fields)
        if word in ("DECIMAL", "NUMERIC"):
            self.need("(")
            self.space()
            match = re.match(r"(\d+)\s*,\s*(\d+)\s*\)", self.value[self.position :])
            if match is None:
                raise ValueError("invalid decimal parameters")
            self.position += len(match[0])
            precision, scale = map(int, match.groups())
            if not 0 <= scale <= precision <= 38:
                raise ValueError("invalid decimal bounds")
            return pa.decimal128(precision, scale)
        scalars = {
            "BOOLEAN": pa.bool_(),
            "TINYINT": pa.int8(),
            "SMALLINT": pa.int16(),
            "INT": pa.int32(),
            "INTEGER": pa.int32(),
            "BIGINT": pa.int64(),
            "FLOAT": pa.float32(),
            "DOUBLE": pa.float64(),
            "STRING": pa.string(),
            "BINARY": pa.binary(),
            "DATE": pa.date32(),
            "TIMESTAMP": pa.timestamp("us", "UTC"),
            "TIMESTAMP_NTZ": pa.timestamp("us"),
        }
        if word not in scalars:
            raise ValueError("unsupported catalog type")
        return scalars[word]


def databricks_type(catalog: str, column: str) -> pa.DataType:
    """Parse static full_data_type catalog syntax; never inspect row values for types."""
    try:
        parser = _Catalog(catalog)
        result = parser.kind()
        parser.space()
        if parser.position != len(catalog):
            raise ValueError("trailing catalog text")
        return result
    except (ValueError, pa.ArrowException) as exc:
        raise ValueError(f"column {column!r}: invalid Databricks catalog type: {exc}") from None
