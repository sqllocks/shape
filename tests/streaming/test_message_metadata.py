"""W9-08 message metadata acceptance (offline)."""

from datetime import UTC, datetime

import pyarrow as pa
import pytest

from shape.streaming.emit.formats import with_event_fields
from shape.streaming.messages import StreamMessage, decode_messages


def batch():
    return with_event_fields(
        pa.RecordBatch.from_pydict(
            {
                "customer": ["a", "b"],
                "region": [1, 2],
                "when": pa.array([datetime(2026, 1, 1, tzinfo=UTC)] * 2),
            }
        ),
        "orders",
        0,
    )


def test_wanted3_binary_metadata_nested_and_flat():
    msg = StreamMessage(
        "0",
        0,
        b'{"x":1,"_shape_key":"spoof"}',
        123000,
        key=b"\xff",
        headers={"trace": b"\x00"},
        properties={"p": b"x"},
        partition_key="a",
    )
    b = decode_messages([msg], with_key=True, with_headers=True, with_timestamp=True)
    assert b.column("_shape_key").to_pylist() == [b"\xff"]
    assert b.column("_shape_headers").type == pa.map_(pa.string(), pa.binary())
    assert b.column("_shape_timestamp").cast(pa.int64()).to_pylist() == [123000]
    assert b.column("_shape_partition_key").to_pylist() == ["a"]
    flat = decode_messages([msg], with_headers=True, nested=False)
    assert flat.column("_shape_headers").type == pa.string()


def test_wanted3_null_metadata_stable_schema():
    b = decode_messages(
        [StreamMessage("0", 0, '{"x":1}')], with_key=True, with_headers=True, with_timestamp=True
    )
    assert b.column("_shape_key").type == pa.binary()
    assert b.column("_shape_key").to_pylist() == [None]


def test_wanted4_cli_options():
    from shape.cli.main import _build_parser

    p = _build_parser()
    a = p.parse_args(
        ["stream-profile", "kafka://b/t", "--with-key", "--with-headers", "--with-timestamp"]
    )
    assert a.with_key and a.with_headers and a.with_timestamp
    a = p.parse_args(
        [
            "emit",
            "retail",
            "--key",
            "customer",
            "--header",
            "x=@region",
            "--partition",
            "0",
            "--timestamp",
            "event_time",
        ]
    )
    assert a.key == "customer" and a.header == ["x=@region"]


def test_wanted3_drift_ignores_metadata_unless_explicitly_selected():
    import shape
    from shape.capture import capture_rows

    before = capture_rows([{"x": 1, "_shape_key": "a"}]).to_dict()
    after = capture_rows([{"x": 1}]).to_dict()
    assert not shape.diff(before, after).drifted
    assert shape.diff(before, after, only_columns=["_shape_key"]).drifted


def test_wanted5_documented_options():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for path in (
        "docs/EMIT.md",
        "docs/CLI.md",
        "plugins/shape-kafka/README.md",
        "plugins/shape-eventhubs/README.md",
        "CHANGELOG.md",
    ):
        text = (root / path).read_text()
        assert "W9-08" in text


def test_wanted3_rejected_rows_keep_metadata_alignment():
    msgs = [
        StreamMessage("0", 0, '{"x":1}', key=b"first"),
        StreamMessage("0", 1, '{"x":"bad"}', key=b"rejected"),
        StreamMessage("0", 2, '{"x":2}', key=b"last"),
    ]
    schema = pa.schema(
        [
            ("x", pa.int64()),
            ("_shape_key", pa.binary()),
            ("_shape_event_time", pa.timestamp("us", tz="UTC")),
        ]
    )
    result = decode_messages(msgs, schema=schema, with_key=True)
    assert result.column("_shape_key").to_pylist() == [b"first", b"last"]


def test_wanted3_profile_persistence_metadata_compatibility(tmp_path):
    import shape

    message = StreamMessage("0", 0, '{"x":1}', key=b"a", headers={"trace": b"\xff"})
    decoded = decode_messages(
        [message], with_key=True, with_headers=True, with_timestamp=True, nested=False
    )
    profile = shape.profile(pa.Table.from_batches([decoded]))
    path = tmp_path / "metadata.shape"
    shape.save(profile, path)
    loaded = shape.load(path)
    assert set(loaded.to_dict()["columns"]) == set(profile.to_dict()["columns"])
    assert loaded.to_dict()["row_count"] == 1


@pytest.mark.parametrize(
    "option,name,type_",
    [
        ("with_key", "_shape_key", pa.binary()),
        ("with_headers", "_shape_headers", pa.map_(pa.string(), pa.binary())),
        ("with_timestamp", "_shape_timestamp", pa.timestamp("us", tz="UTC")),
    ],
)
def test_explicit_payload_schema_retains_requested_metadata_and_alignment(option, name, type_):
    messages = [
        StreamMessage("0", 0, '{"x":1}', 1, key=b"first", headers={"h": b"first"}),
        StreamMessage("0", 1, '{"x":"bad"}', 2, key=b"bad", headers={"h": b"bad"}),
        StreamMessage("0", 2, '{"x":2}', 3, key=b"last", headers={"h": b"last"}),
    ]
    result = decode_messages(messages, schema=pa.schema([("x", pa.int64())]), **{option: True})
    assert result.schema.names == ["x", name]
    assert result.schema.field(name).type == type_
    assert result.column("x").to_pylist() == [1, 2]
    if option == "with_key":
        assert result.column(name).to_pylist() == [b"first", b"last"]
    elif option == "with_headers":
        assert result.column(name).to_pylist() == [[("h", b"first")], [("h", b"last")]]
    else:
        assert result.column(name).cast(pa.int64()).to_pylist() == [1, 3]


def test_explicit_schema_cannot_coerce_reserved_binary_metadata():
    message = StreamMessage("0", 0, '{"x":1,"_shape_key":"spoof"}', key=b"\xff")
    result = decode_messages(
        [message], schema=pa.schema([("x", pa.int64()), ("_shape_key", pa.string())]), with_key=True
    )
    assert result.column("_shape_key").type == pa.binary()
    assert result.column("_shape_key").to_pylist() == [b"\xff"]
