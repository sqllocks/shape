"""#260: `LogicalType` refuses a kind it does not know and an impossible bit width, and every
invalid type raises `ShapeTypeError` (still a `ValueError`, so existing handlers keep working)."""

from __future__ import annotations

import pyarrow as pa
import pytest

from shape.errors import ShapeError, ShapeTypeError
from shape.types import LogicalType, from_arrow_type


@pytest.mark.parametrize(
    "kwargs",
    [
        {"kind": "bogus"},
        {"kind": "int", "bit_width": 7},
        {"kind": "uint", "bit_width": 128},
        {"kind": "float", "bit_width": 8},
        {"kind": "decimal"},
        {"kind": "decimal", "precision": 10},
        {"kind": "list"},
        {"kind": "map", "value_type": LogicalType("string")},
    ],
)
def test_an_invalid_logical_type_is_a_shape_type_error(kwargs):
    with pytest.raises(ShapeTypeError) as caught:
        LogicalType(**kwargs)
    assert isinstance(caught.value, ValueError) and isinstance(caught.value, ShapeError)


@pytest.mark.parametrize(
    "arrow",
    [
        pa.bool_(), pa.int8(), pa.int16(), pa.int32(), pa.int64(), pa.uint8(), pa.uint16(),
        pa.uint32(), pa.uint64(), pa.float32(), pa.float64(), pa.decimal128(10, 2),
        pa.string(), pa.large_string(), pa.binary(), pa.large_binary(), pa.date32(),
        pa.date64(), pa.time32("ms"), pa.time64("us"), pa.timestamp("ns", tz="UTC"),
        pa.duration("s"), pa.list_(pa.int32()), pa.large_list(pa.string()),
        pa.struct([pa.field("a", pa.int8())]), pa.map_(pa.string(), pa.int64()),
        pa.binary(16),
    ],
)  # fmt: skip
def test_every_type_from_arrow_is_valid(arrow):
    converted = from_arrow_type(arrow)
    assert LogicalType(**{f: getattr(converted, f) for f in converted.__slots__}) == converted
