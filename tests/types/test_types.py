import pyarrow as pa
import pytest

from shape.errors import ShapeTypeError
from shape.types import from_arrow_type, schema_from_arrow


def test_preserves_decimal_precision_scale():
    t = from_arrow_type(pa.decimal128(30, 9))
    assert (t.kind, t.precision, t.scale) == ("decimal", 30, 9)


def test_preserves_timestamp_timezone_and_unit():
    t = from_arrow_type(pa.timestamp("us", tz="America/New_York"))
    assert (t.kind, t.unit, t.timezone) == ("timestamp", "us", "America/New_York")


def test_nested_struct_and_list():
    schema = pa.schema(
        [pa.field("x", pa.struct([pa.field("ids", pa.list_(pa.int64()), nullable=False)]))]
    )
    fields = schema_from_arrow(schema)
    nested = fields[0].logical_type.fields[0]
    assert nested.name == "ids"
    assert nested.nullable is False
    assert nested.logical_type.value_type.bit_width == 64


def test_unsupported_type_is_typed_error():
    with pytest.raises(ShapeTypeError):
        from_arrow_type(pa.null())
