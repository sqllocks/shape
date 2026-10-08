"""Immutable logical type model and Arrow conversion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .errors import ShapeTypeError

if TYPE_CHECKING:
    import pyarrow as pa  # type: ignore[import-untyped]


#: Every logical kind, with the bit widths it may have (empty: any, or none given).
KINDS: dict[str, tuple[int, ...]] = {
    "boolean": (),
    "int": (8, 16, 32, 64),
    "uint": (8, 16, 32, 64),
    "float": (16, 32, 64),
    "decimal": (),
    "string": (),
    "large_string": (),
    "binary": (),
    "large_binary": (),
    "date": (),
    "time": (),
    "timestamp": (),
    "duration": (),
    "list": (),
    "large_list": (),
    "struct": (),
    "map": (),
    "fixed_binary": (),
}


@dataclass(frozen=True, slots=True)
class LogicalType:
    """A logical column type, independent of its physical encoding.

    ``kind`` names the type family, one of :data:`KINDS`; :func:`from_arrow_type` produces
    ``boolean``, ``int``, ``uint``, ``float``, ``decimal``, ``string``, ``large_string``,
    ``binary``, ``large_binary``, ``date``, ``time``, ``timestamp``, ``duration``, ``list``,
    ``large_list``, ``struct``, ``map`` and ``fixed_binary``. The other fields apply per kind:
    ``bit_width`` (integers, floats, ``fixed_binary``), ``precision`` and ``scale``
    (``decimal``), ``unit`` and ``timezone`` (temporal kinds), ``value_type`` (lists and maps),
    ``key_type`` (maps) and ``fields`` (structs).

    Raises :class:`shape.errors.ShapeTypeError` (also a ``ValueError``) for an unknown kind, a bit
    width the kind does not have, ``decimal`` without ``precision`` or ``scale``, a list without
    ``value_type`` or a map without ``key_type`` or ``value_type`` (issue #260).
    """

    kind: str
    bit_width: int | None = None
    precision: int | None = None
    scale: int | None = None
    unit: str | None = None
    timezone: str | None = None
    value_type: LogicalType | None = None
    key_type: LogicalType | None = None
    fields: tuple[FieldType, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ShapeTypeError(
                f"unknown logical type kind {self.kind!r}; the kinds are: {', '.join(KINDS)}"
            )
        widths = KINDS[self.kind]
        if self.bit_width is not None and widths and self.bit_width not in widths:
            raise ShapeTypeError(
                f"{self.kind} has no bit width {self.bit_width} "
                f"(it has: {', '.join(map(str, widths))})"
            )
        if self.kind == "decimal" and (self.precision is None or self.scale is None):
            raise ShapeTypeError("decimal requires precision and scale")
        if self.kind in {"list", "large_list"} and self.value_type is None:
            raise ShapeTypeError(f"{self.kind} requires value_type")
        if self.kind == "map" and (self.key_type is None or self.value_type is None):
            raise ShapeTypeError("map requires key_type and value_type")


@dataclass(frozen=True, slots=True)
class FieldType:
    """A named, typed field: a column of a schema or a member of a ``struct``."""

    name: str
    logical_type: LogicalType
    nullable: bool = True


def from_arrow_type(data_type: pa.DataType) -> LogicalType:
    """Convert an Arrow physical type to a :class:`LogicalType` without silent narrowing.

    Nested types (lists, structs, maps) convert recursively. A 16-byte fixed-size binary becomes
    ``fixed_binary`` (128 bits). Any other type, such as ``null``, ``float16``, a dictionary,
    a union or another fixed-size binary, raises :class:`shape.errors.ShapeTypeError`.
    """
    import pyarrow as pa

    if pa.types.is_boolean(data_type):
        return LogicalType("boolean")
    if pa.types.is_int8(data_type):
        return LogicalType("int", bit_width=8)
    if pa.types.is_int16(data_type):
        return LogicalType("int", bit_width=16)
    if pa.types.is_int32(data_type):
        return LogicalType("int", bit_width=32)
    if pa.types.is_int64(data_type):
        return LogicalType("int", bit_width=64)
    if pa.types.is_uint8(data_type):
        return LogicalType("uint", bit_width=8)
    if pa.types.is_uint16(data_type):
        return LogicalType("uint", bit_width=16)
    if pa.types.is_uint32(data_type):
        return LogicalType("uint", bit_width=32)
    if pa.types.is_uint64(data_type):
        return LogicalType("uint", bit_width=64)
    if pa.types.is_float32(data_type):
        return LogicalType("float", bit_width=32)
    if pa.types.is_float64(data_type):
        return LogicalType("float", bit_width=64)
    if pa.types.is_decimal(data_type):
        return LogicalType("decimal", precision=data_type.precision, scale=data_type.scale)
    if pa.types.is_string(data_type):
        return LogicalType("string")
    if pa.types.is_large_string(data_type):
        return LogicalType("large_string")
    if pa.types.is_binary(data_type):
        return LogicalType("binary")
    if pa.types.is_large_binary(data_type):
        return LogicalType("large_binary")
    if pa.types.is_date32(data_type):
        return LogicalType("date", unit="day")
    if pa.types.is_date64(data_type):
        return LogicalType("date", unit="ms")
    if pa.types.is_time32(data_type) or pa.types.is_time64(data_type):
        return LogicalType("time", unit=data_type.unit)
    if pa.types.is_timestamp(data_type):
        return LogicalType("timestamp", unit=data_type.unit, timezone=data_type.tz)
    if pa.types.is_duration(data_type):
        return LogicalType("duration", unit=data_type.unit)
    if pa.types.is_list(data_type):
        return LogicalType("list", value_type=from_arrow_type(data_type.value_type))
    if pa.types.is_large_list(data_type):
        return LogicalType("large_list", value_type=from_arrow_type(data_type.value_type))
    if pa.types.is_struct(data_type):
        return LogicalType(
            "struct",
            fields=tuple(FieldType(f.name, from_arrow_type(f.type), f.nullable) for f in data_type),
        )
    if pa.types.is_map(data_type):
        return LogicalType(
            "map",
            key_type=from_arrow_type(data_type.key_type),
            value_type=from_arrow_type(data_type.item_type),
        )
    if pa.types.is_fixed_size_binary(data_type) and data_type.byte_width == 16:
        # Physical representation only; semantic UUID annotation is separate.
        return LogicalType("fixed_binary", bit_width=128)
    raise ShapeTypeError(f"Unsupported Arrow type: {data_type}")


def schema_from_arrow(schema: pa.Schema) -> tuple[FieldType, ...]:
    """Convert an Arrow schema to one :class:`FieldType` per field, in order, keeping each
    field's name and nullability; raises :class:`shape.errors.ShapeTypeError` as
    :func:`from_arrow_type` does."""
    return tuple(FieldType(f.name, from_arrow_type(f.type), f.nullable) for f in schema)
