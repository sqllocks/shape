"""Static native catalogs and schema-guided JSON source value restoration."""

import datetime as dt
from decimal import Decimal

import pyarrow as pa
import pytest
from shape_databases.nested import native_json
from shape_databases.nested_readback import databricks_type, restore_json_column


def test_restore_json_roundtrip_uses_original_schema_and_preserves_values():
    kind = pa.struct(
        [
            ("amount", pa.decimal128(15, 4)),
            ("when", pa.timestamp("us", "UTC")),
            ("bytes", pa.binary()),
            ("text", pa.string()),
            ("entries", pa.map_(pa.int32(), pa.list_(pa.int16()))),
        ]
    )
    field = pa.field("payload", kind)
    expected = pa.array(
        [
            {
                "amount": Decimal("1234567.2300"),
                "when": dt.datetime(2026, 1, 2, 3, 4, tzinfo=dt.timezone(dt.timedelta(hours=5))),
                "bytes": b"\x00\xff",
                "text": '{"looks":"json"}',
                "entries": [(1, [2, None]), (1, []), (2, None)],
            },
            None,
        ],
        kind,
    )
    raw = [native_json(value, field, "postgres") for value in expected.to_pylist()]
    assert restore_json_column(raw, field).equals(expected)


@pytest.mark.parametrize(
    "raw",
    [
        '"{\\"value\\":1}"',
        '{"value":1,"value":2}',
        '{"value":true}',
        '{"value":"1"}',
        '{"value":1,"extra":2}',
        "{}",
        "{broken",
        '{"value":NaN}',
        '{"value":Infinity}',
        "[]",
    ],
)
def test_restore_json_refuses_corrupt_ambiguous_or_mismatched_source_output(raw):
    with pytest.raises(ValueError, match="payload"):
        restore_json_column([raw], pa.field("payload", pa.struct([("value", pa.int32())])))


def test_restore_json_null_empty_and_object_map_with_json_looking_child():
    field = pa.field("payload", pa.map_(pa.string(), pa.string()))
    actual = restore_json_column([None, "{}", '{"a":"[]"}'], field)
    assert actual.to_pylist() == [None, [], [("a", "[]")]]
    assert restore_json_column(
        ["[]", "null"], pa.field("payload", pa.list_(pa.int32()))
    ).to_pylist() == [[], None]


@pytest.mark.parametrize(
    "kind,raw",
    [
        (pa.list_(pa.int8()), "[128]"),
        (pa.list_(pa.decimal128(4, 2)), '["1.234"]'),
        (pa.list_(pa.binary()), '["not base64"]'),
        (pa.list_(pa.timestamp("us", "UTC")), '["2026-01-02T03:04:05"]'),
        (pa.struct([pa.field("x", pa.int32(), nullable=False)]), '{"x":null}'),
        (pa.map_(pa.int32(), pa.string()), '[[null,"x"]]'),
    ],
)
def test_restore_json_refuses_loss_or_invalid_child_values(kind, raw):
    with pytest.raises(ValueError, match="payload"):
        restore_json_column([raw], pa.field("payload", kind))


@pytest.mark.parametrize(
    "catalog,expected",
    [
        (
            "ARRAY<STRUCT<`amount`: DECIMAL(12,4), `at`: TIMESTAMP_NTZ, `odd``name`: BINARY>>",
            pa.list_(
                pa.struct(
                    [
                        ("amount", pa.decimal128(12, 4)),
                        ("at", pa.timestamp("us")),
                        ("odd`name", pa.binary()),
                    ]
                )
            ),
        ),
        (
            "map<string,array<struct<x:int,y:timestamp>>>",
            pa.map_(
                pa.string(),
                pa.list_(pa.struct([("x", pa.int32()), ("y", pa.timestamp("us", "UTC"))])),
            ),
        ),
        ("ARRAY<BOOLEAN>", pa.list_(pa.bool_())),
        ("ARRAY<DOUBLE>", pa.list_(pa.float64())),
    ],
)
def test_databricks_full_catalog_type_preserves_declared_children(catalog, expected):
    assert databricks_type(catalog, "payload") == expected


@pytest.mark.parametrize(
    "catalog",
    [
        "ARRAY<NOTATYPE>",
        "STRUCT<x:INT,x:INT>",
        "ARRAY<INT> garbage",
        "MAP<INT,STRING>",
        "DECIMAL(3,5)",
        "STRUCT<>",
        "ARRAY<INT",
    ],
)
def test_databricks_invalid_catalog_is_named_and_never_inferred(catalog):
    with pytest.raises(ValueError, match="payload"):
        databricks_type(catalog, "payload")


@pytest.mark.parametrize(
    "kind,text,expected",
    [
        (pa.timestamp("ns"), "2026-01-02T03:04:05.123456789", 1767323045123456789),
        (pa.time64("ns"), "03:04:05.123456789", 11045123456789),
    ],
)
def test_restore_exact_nanosecond_temporal_values(kind, text, expected):
    result = restore_json_column(['["' + text + '"]'], pa.field("payload", pa.list_(kind)))
    assert result.values.cast(pa.int64()).to_pylist() == [expected]


@pytest.mark.parametrize(
    "kind,text",
    [
        (pa.timestamp("us"), "2026-01-02T03:04:05.123456789"),
        (pa.time64("us"), "03:04:05.123456789"),
    ],
)
def test_restore_refuses_temporal_precision_loss(kind, text):
    with pytest.raises(ValueError, match="payload"):
        restore_json_column(['["' + text + '"]'], pa.field("payload", pa.list_(kind)))
