"""W9-09 offline binary format contracts, including wire compatibility."""

import io
import json

import fastavro
import pyarrow as pa
import pytest

from shape.errors import ShapeError
from shape.streaming.messages import DecodeStats, StreamMessage, decode_messages

AVRO = json.dumps(
    {
        "type": "record",
        "name": "Order",
        "fields": [
            {"name": "id", "type": "long"},
            {
                "name": "child",
                "type": {
                    "type": "record",
                    "name": "Child",
                    "fields": [{"name": "n", "type": "long"}],
                },
            },
            {"name": "tags", "type": {"type": "array", "items": "string"}},
            {"name": "attrs", "type": {"type": "map", "values": "long"}},
        ],
    }
)
PROTO = """syntax = "proto3"; package shop;
message Order {
 int64 id = 1; Child child = 2; repeated string tags = 3; map<string,int64> attrs = 4;
}
message Child { int64 n = 1; }
"""
ROW = {"id": 7, "child": {"n": 8}, "tags": ["a", "b"], "attrs": {"k": 9}}
SCHEMA = pa.schema(
    [
        pa.field("id", pa.int64()),
        pa.field("child", pa.struct([("n", pa.int64())])),
        pa.field("tags", pa.list_(pa.string())),
        pa.field("attrs", pa.map_(pa.string(), pa.int64())),
    ]
)


def codec(fmt):
    from shape.streaming.binary import BinaryCodec

    return BinaryCodec(fmt, AVRO if fmt == "avro" else PROTO, message="shop.Order")


@pytest.mark.parametrize("fmt", ["avro", "protobuf"])
def test_plain_binary_nested_roundtrip_and_preflight(fmt):
    c = codec(fmt)
    c.validate_schema(SCHEMA)
    payload = c.encode(ROW)
    assert c.decode(payload) == ROW
    empty = {"id": 0, "child": {"n": 0}, "tags": [], "attrs": {}}
    assert c.decode(c.encode(empty)) == empty
    with pytest.raises(ShapeError, match="id"):
        c.validate_schema(SCHEMA.set(0, pa.field("id", pa.string())))
    with pytest.raises(ShapeError, match="missing"):
        c.validate_schema(SCHEMA.remove(0))
    with pytest.raises(ValueError):
        c.decode(b"\xff")


def test_plain_avro_is_compatible_with_independent_reader():
    c = codec("avro")
    assert fastavro.schemaless_reader(io.BytesIO(c.encode(ROW)), json.loads(AVRO)) == ROW


@pytest.mark.parametrize("fmt", ["avro", "protobuf"])
def test_typed_decode_rejects_and_counts_with_reader_schema(fmt):
    c = codec(fmt)
    stats = DecodeStats()
    batch = decode_messages(
        [StreamMessage("0", 0, c.encode(ROW)), StreamMessage("0", 1, b"\xff")],
        decoder=c.decode,
        schema=SCHEMA.append(pa.field("_shape_event_time", pa.timestamp("us", tz="UTC"))),
        stats=stats,
    )
    assert batch.num_rows == 1
    assert batch.schema.field("child").type == SCHEMA.field("child").type
    assert batch.schema.field("attrs").type == SCHEMA.field("attrs").type
    assert stats.to_dict() == {"messages": 2, "rows": 1, "undecodable": 1, "rejected": 0}
    bad = DecodeStats()
    assert (
        decode_messages(
            [StreamMessage("0", 0, c.encode(ROW))],
            decoder=c.decode,
            schema=pa.schema(
                [("id", pa.bool_()), ("_shape_event_time", pa.timestamp("us", tz="UTC"))]
            ),
            stats=bad,
        )
        is None
    )
    assert bad.rejected == 1


def test_registry_avro_resolves_each_id_once_and_checks_prefix():
    from shape.streaming.binary import RegistryAvroDecoder

    calls = []

    def resolve(schema_id):
        calls.append(schema_id)
        return AVRO

    decode = RegistryAvroDecoder(resolve)
    payload = codec("avro").encode(ROW)
    for sid in [1, 2, 1]:
        assert decode(b"\x00" + sid.to_bytes(4, "big") + payload) == ROW
    assert calls == [1, 2]
    for bad in [b"", b"\x00\x00", b"\x01\x00\x00\x00\x01"]:
        with pytest.raises(ValueError):
            decode(bad)


def test_protobuf_requires_message_and_refuses_unsupported_types():
    from shape.streaming.binary import BinaryCodec

    for message in [None, "missing"]:
        with pytest.raises(ShapeError, match="message"):
            BinaryCodec("protobuf", PROTO, message=message)
    with pytest.raises(ShapeError):
        BinaryCodec(
            "protobuf", 'syntax = "proto3"; message X { google.protobuf.Any a = 1; }', message="X"
        )


def test_azure_registry_encoder_contract_cached_read():
    from shape.streaming.binary import AzureAvroCodec

    class Encoder:
        def encode(self, row, *, schema):
            assert schema == AVRO
            return {"content": codec("avro").encode(row), "content_type": "avro/binary+abc"}

        def decode(self, message):
            assert message["content_type"] == "avro/binary+abc"
            return codec("avro").decode(message["content"])

    c = AzureAvroCodec(Encoder(), AVRO)
    message = c.encode(ROW)
    assert c.decode(message["content"], message["content_type"]) == ROW
    with pytest.raises(ValueError):
        c.decode(message["content"], "application/json")


@pytest.mark.parametrize("broker", ["kafka", "eventhubs"])
@pytest.mark.parametrize("fmt", ["avro", "protobuf"])
def test_both_brokers_read_write_plain_nested_and_preflight(tmp_path, broker, fmt):
    from shape.streaming.emit.formats import with_event_fields

    c = codec(fmt)
    path = tmp_path / ("events.avsc" if fmt == "avro" else "events.proto")
    path.write_text(c.text)
    batch = with_event_fields(pa.RecordBatch.from_pylist([ROW], schema=SCHEMA), "orders", 0)
    opts = {"schema_file": str(path), "message": "shop.Order"}
    if broker == "kafka":
        from shape_kafka.testing import EmitterHarness, FakeBroker

        harness = EmitterHarness()
        emitter = harness.make()
        assert emitter.emit(harness.uri, [batch], event_format=fmt, **opts) == 1
        body = harness.store.log[0][2]
        source = FakeBroker({"orders": {0: [(body, None), (b"\xff", None)]}}).source()
        out = list(source.read("kafka://localhost/orders", format=fmt, **opts))

        def sent_count():
            return len(harness.store.log)
    else:
        from shape_eventhubs.testing import EmitterHarness, FakeHub

        harness = EmitterHarness()
        emitter = harness.make()
        assert emitter.emit(harness.uri, [batch], event_format=fmt, **opts) == 1
        event = harness.hub.batches[0].events[0]
        body = b"".join(event.body)
        source = FakeHub({"0": [(body, None), (b"\xff", None)]}).source()
        out = list(source.read("eventhubs://localhost/orders", format=fmt, **opts))

        def sent_count():
            return sum(len(b.events) for b in harness.hub.batches)

    assert c.decode(body) == ROW
    assert len(out) == 1
    assert out[0][1].num_rows == 1
    assert out[0][1].schema.field("attrs").type == c.schema.field("attrs").type
    assert source.stats.undecodable == 1
    before = sent_count()
    bad = with_event_fields(pa.RecordBatch.from_pydict({"id": ["bad"]}), "orders", 0)
    with pytest.raises(ShapeError, match="id|missing"):
        emitter.emit(harness.uri, [bad], event_format=fmt, **opts)
    assert sent_count() == before


def test_kafka_registry_source_evolution_cache_and_pinned_schema():
    from shape_kafka.testing import FakeBroker

    from shape.streaming.binary import BinaryCodec

    old = json.dumps(
        {"type": "record", "name": "Order", "fields": [{"name": "id", "type": "long"}]}
    )
    new = json.dumps(
        {"type": "record", "name": "Order", "fields": [{"name": "id", "type": "string"}]}
    )
    calls = []

    def transport(method, url, headers, body):
        assert method == "GET"
        sid = int(url.rsplit("/", 1)[1])
        calls.append(sid)
        return 200, json.dumps({"schema": {1: old, 2: new}[sid]}).encode()

    def wire(sid, text, row):
        return b"\x00" + sid.to_bytes(4, "big") + BinaryCodec("avro", text).encode(row)

    source = FakeBroker(
        {
            "orders": {
                0: [
                    (wire(1, old, {"id": 1}), None),
                    (wire(2, new, {"id": "bad"}), None),
                    (wire(1, old, {"id": 2}), None),
                ]
            }
        },
        chunk=1,
    ).source()
    schema = pa.schema([("id", pa.int64()), ("_shape_event_time", pa.timestamp("us", tz="UTC"))])
    out = list(
        source.read(
            "kafka://localhost/orders",
            format="avro",
            schema_registry_url="https://registry.test",
            registry_transport=transport,
            schema=schema,
        )
    )
    assert [v for _, b in out for v in b.column("id").to_pylist()] == [1, 2]
    assert calls == [1, 2]
    assert source.stats.rejected == 1


def test_azure_registry_both_directions_against_fake_encoder(tmp_path):
    from shape_eventhubs.testing import EmitterHarness, FakeHub

    from shape.streaming.emit.formats import with_event_fields

    class Encoder:
        def encode(self, row, *, schema):
            return {"content": codec("avro").encode(row), "content_type": "avro/binary+id1"}

        def decode(self, message):
            assert message["content_type"] == "avro/binary+id1"
            return codec("avro").decode(message["content"])

    path = tmp_path / "events.avsc"
    path.write_text(AVRO)
    h = EmitterHarness()
    h.make().emit(
        h.uri,
        [with_event_fields(pa.RecordBatch.from_pylist([ROW], schema=SCHEMA), "orders", 0)],
        event_format="avro",
        schema_file=str(path),
        schema_registry_url="registry.servicebus.windows.net",
        schema_registry_group="events",
        registry_encoder=Encoder(),
    )
    event = h.hub.batches[0].events[0]
    assert event.content_type == "avro/binary+id1"
    hub = FakeHub({"0": [(b"".join(event.body), None)]})
    hub.partitions["0"][0].content_type = event.content_type
    out = list(
        hub.source().read(
            "eventhubs://localhost/orders",
            format="avro",
            schema_registry_url="registry.servicebus.windows.net",
            registry_encoder=Encoder(),
            schema=SCHEMA.append(pa.field("_shape_event_time", pa.timestamp("us", tz="UTC"))),
        )
    )
    assert out[0][1].num_rows == 1
    with pytest.raises(ShapeError, match="credentials"):
        list(
            hub.source().read(
                "eventhubs://localhost/orders",
                format="avro",
                schema_registry_url="https://user:password@registry.test",
                registry_encoder=Encoder(),
            )
        )


def test_protobuf_wire_compatibility_preserves_declared_enum_numbers():
    from shape.streaming.binary import BinaryCodec

    text = (
        'syntax = "proto3";\nmessage E { enum State { UNKNOWN = 0; READY = 7; } State state = 1; }'
    )
    c = BinaryCodec("protobuf", text, message="E")
    assert c.encode({"state": "READY"}) == b"\x08\x07"
    assert c.decode(b"\x08\x07") == {"state": "READY"}


@pytest.mark.conformance
@pytest.mark.parametrize("fmt", ["avro", "protobuf"])
def test_binary_nested_reader_never_coerces_bool_to_integer(fmt):
    """SHAPE-STREAM-009: typed binary rows reject a nested lossy conversion."""
    stats = DecodeStats()
    schema = pa.schema(
        [
            ("child", pa.struct([("n", pa.int64())])),
            ("_shape_event_time", pa.timestamp("us", tz="UTC")),
        ]
    )
    batch = decode_messages(
        [StreamMessage("0", 0, b"unused")],
        decoder=lambda _: {"child": {"n": True}},
        schema=schema,
        stats=stats,
    )
    assert batch is None
    assert stats.rejected == 1


def test_binary_format_docs_and_type_tables_are_complete():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for path in [
        "docs/EMIT.md",
        "docs/CLI.md",
        "plugins/shape-kafka/README.md",
        "plugins/shape-eventhubs/README.md",
    ]:
        text = (root / path).read_text()
        for term in ["schema_file", "message", "avro", "protobuf"]:
            assert term in text, (path, term)
    for path in ["plugins/shape-kafka/README.md", "plugins/shape-eventhubs/README.md"]:
        text = (root / path).read_text()
        assert "struct" in text and "map" in text and "list" in text


def test_eventhub_sdk_body_is_kept_as_binary_even_when_utf8():
    from azure.eventhub import EventData
    from shape_eventhubs.source import _body

    body = codec("avro").encode(ROW)
    body.decode("utf8")  # This valid binary payload also happens to be valid UTF-8.
    assert _body(EventData(body)) == body


def test_protobuf_repeated_and_map_enums_keep_names():
    from shape.streaming.binary import BinaryCodec

    text = """syntax = "proto3";
    enum State { UNKNOWN = 0; READY = 7; }
    message E { repeated State states = 1; map<string,State> attrs = 2; }
    """
    c = BinaryCodec("protobuf", text, message="E")
    row = {"states": ["READY", "UNKNOWN"], "attrs": {"x": "READY"}}
    body = c.encode(row)
    assert c.decode(body) == row
    stats = DecodeStats()
    batch = decode_messages([StreamMessage("0", 0, body)], decoder=c.decode, stats=stats)
    assert batch.num_rows == 1
    assert batch.schema.field("attrs").type == c.schema.field("attrs").type
    assert stats.rejected == 0


@pytest.mark.parametrize("chunk", [1, 3])
def test_unpinned_registry_evolution_promotes_widened_integer(chunk):
    from shape_kafka.testing import FakeBroker

    from shape.streaming.binary import BinaryCodec

    texts = {
        sid: json.dumps({"type": "record", "name": "E", "fields": [{"name": "id", "type": kind}]})
        for sid, kind in [(1, "int"), (2, "long")]
    }

    def transport(method, url, headers, body):
        return 200, json.dumps({"schema": texts[int(url.rsplit("/", 1)[1])]}).encode()

    values = [(1, 1), (2, 2**40), (1, 3)]
    bodies = [
        (
            b"\x00"
            + sid.to_bytes(4, "big")
            + BinaryCodec("avro", texts[sid]).encode({"id": value}),
            None,
        )
        for sid, value in values
    ]
    source = FakeBroker({"orders": {0: bodies}}, chunk=chunk).source()
    out = list(
        source.read(
            "kafka://localhost/orders",
            format="avro",
            schema_registry_url="https://registry.test",
            registry_transport=transport,
        )
    )
    assert [v for _, batch in out for v in batch.column("id").to_pylist()] == [1, 2**40, 3]
    assert source.stats.rows == 3 and source.stats.rejected == 0


@pytest.mark.parametrize("value", ["bad", 42, True, [1], [[1]], [[1, 2, 3]]])
def test_reader_map_mismatch_rejects_and_counts_instead_of_raising(value):
    schema = pa.schema(
        [
            ("attrs", pa.map_(pa.string(), pa.int64())),
            ("_shape_event_time", pa.timestamp("us", tz="UTC")),
        ]
    )
    stats = DecodeStats()
    assert (
        decode_messages(
            [StreamMessage("0", 0, b"unused")],
            decoder=lambda _: {"attrs": value},
            schema=schema,
            stats=stats,
        )
        is None
    )
    assert stats.rejected == 1


@pytest.mark.parametrize(
    "kinds,values", [(["string", "long"], ["text", 7]), (["long", "string"], [7, "text"])]
)
def test_unpinned_incompatible_writer_schemas_yield_separate_batches_and_offsets(kinds, values):
    from shape_kafka.testing import FakeBroker

    from shape.streaming.binary import BinaryCodec

    texts = {
        i: json.dumps({"type": "record", "name": "E", "fields": [{"name": "id", "type": kind}]})
        for i, kind in enumerate(kinds, 1)
    }

    def transport(method, url, headers, body):
        return 200, json.dumps({"schema": texts[int(url.rsplit("/", 1)[1])]}).encode()

    bodies = [
        (
            b"\x00"
            + sid.to_bytes(4, "big")
            + BinaryCodec("avro", texts[sid]).encode({"id": value}),
            None,
        )
        for sid, value in enumerate(values, 1)
    ]
    source = FakeBroker({"orders": {0: bodies}}).source()
    out = list(
        source.read(
            "kafka://localhost/orders",
            format="avro",
            schema_registry_url="https://registry.test",
            registry_transport=transport,
        )
    )
    assert [v for _, batch in out for v in batch.column("id").to_pylist()] == values
    assert [offset.value for offset, _ in out] == [{"0": 1}, {"0": 2}]
    assert source.stats.rejected == 0


@pytest.mark.parametrize(
    "typ,value",
    [
        (pa.list_(pa.field("item", pa.int64(), nullable=False)), [None]),
        (pa.map_(pa.string(), pa.field("value", pa.int64(), nullable=False)), {"x": None}),
    ],
)
def test_reader_nonnullable_collection_children_are_rejected(typ, value):
    stats = DecodeStats()
    assert (
        decode_messages(
            [StreamMessage("0", 0, b"unused")],
            decoder=lambda _: {"x": value},
            schema=pa.schema([("x", typ)]),
            stats=stats,
        )
        is None
    )
    assert stats.rejected == 1


def test_avro_logical_event_timestamp_is_used_before_broker_time():
    from datetime import UTC, datetime

    from shape.streaming.binary import BinaryCodec

    text = json.dumps(
        {
            "type": "record",
            "name": "E",
            "fields": [
                {
                    "name": "_shape_event_time",
                    "type": {"type": "long", "logicalType": "timestamp-micros"},
                }
            ],
        }
    )
    codec = BinaryCodec("avro", text)
    when = datetime(2026, 1, 1, tzinfo=UTC)
    batch = decode_messages(
        [StreamMessage("0", 0, codec.encode({"_shape_event_time": when}), timestamp_us=0)],
        decoder=codec.decode,
    )
    assert batch.column("_shape_event_time").to_pylist() == [when]


def test_azure_reader_resolves_each_message_schema_once_and_retains_map_types():
    from shape.streaming.binary import AzureAvroCodec

    calls = []

    class Encoder:
        def decode(self, message):
            return codec("avro").decode(message["content"])

    def resolve(schema_id):
        calls.append(schema_id)
        return AVRO

    reader = AzureAvroCodec(Encoder(), resolve=resolve)
    payload = codec("avro").encode(ROW)
    messages = [
        StreamMessage("0", i, payload, content_type=f"avro/binary+{sid}")
        for i, sid in enumerate(["a", "b", "a"])
    ]
    batch = decode_messages(messages, decoder=reader)
    assert calls == ["a", "b"]
    assert batch.num_rows == 3
    assert pa.types.is_map(batch.schema.field("attrs").type)


def test_local_protobuf_validates_documented_logical_wire_types_recursively():
    from datetime import UTC, date, datetime, time
    from decimal import Decimal

    from shape.streaming.binary import BinaryCodec

    text = """syntax = "proto3";
    message E { int32 day = 1; int64 clock = 2; int64 ts = 3; string amount = 4;
      repeated int32 days = 5; Child child = 6; }
    message Child { string amount = 1; }
    """
    c = BinaryCodec("protobuf", text, message="E")
    schema = pa.schema(
        [
            ("day", pa.date32()),
            ("clock", pa.time64("us")),
            ("ts", pa.timestamp("us", tz="UTC")),
            ("amount", pa.decimal128(8, 2)),
            ("days", pa.list_(pa.date32())),
            ("child", pa.struct([("amount", pa.decimal128(8, 2))])),
        ]
    )
    c.validate_schema(schema)
    row = {
        "day": date(1970, 1, 2),
        "clock": time(0, 0, 2),
        "ts": datetime(1970, 1, 1, 0, 0, 3, tzinfo=UTC),
        "amount": Decimal("1.20"),
        "days": [date(1970, 1, 3)],
        "child": {"amount": Decimal("2.30")},
    }
    assert c.decode(c.encode(row)) == {
        "day": 1,
        "clock": 2_000_000,
        "ts": 3_000_000,
        "amount": "1.20",
        "days": [2],
        "child": {"amount": "2.30"},
    }
    for wrong in [pa.float64(), pa.bool_(), pa.binary()]:
        with pytest.raises(ShapeError, match="day"):
            c.validate_schema(schema.set(0, pa.field("day", wrong)))
    incompatible = BinaryCodec(
        "protobuf", 'syntax = "proto3"; message E { bool day = 1; }', message="E"
    )
    with pytest.raises(ShapeError, match="day"):
        incompatible.validate_schema(pa.schema([("day", pa.date32())]))


@pytest.mark.parametrize(
    "fmt,text",
    [
        ("avro", '{"type":"record","name":"E","fields":[{"name":"x","type":"missing"}]}'),
        ("avro", "{bad"),
        ("protobuf", 'syntax = "proto3"; message E { missing x = 1; }'),
    ],
)
def test_invalid_local_schema_files_fail_with_shape_error(tmp_path, fmt, text):
    from shape.streaming.binary import BinaryCodec

    path = tmp_path / "invalid.schema"
    path.write_text(text)
    with pytest.raises(ShapeError):
        BinaryCodec.from_file(fmt, str(path), message="E")
    with pytest.raises(ShapeError, match="cannot read"):
        BinaryCodec.from_file(fmt, str(tmp_path / "missing"), message="E")


def test_protobuf_map_logical_keys_match_validation_and_encoding():
    from datetime import date

    from shape.streaming.binary import BinaryCodec

    c = BinaryCodec(
        "protobuf", 'syntax = "proto3"; message E { map<int32,string> attrs = 1; }', message="E"
    )
    c.validate_schema(pa.schema([("attrs", pa.map_(pa.date32(), pa.string()))]))
    assert c.decode(c.encode({"attrs": [(date(1970, 1, 2), "a")]})) == {"attrs": {1: "a"}}
    with pytest.raises(ShapeError, match="attrs.key"):
        c.validate_schema(pa.schema([("attrs", pa.map_(pa.timestamp("us"), pa.string()))]))


@pytest.mark.parametrize("row", [{"id": None, "tags": []}, {"id": 1, "tags": None}])
def test_protobuf_values_without_presence_cannot_silently_lose_nulls(row):
    from shape.streaming.binary import BinaryCodec

    c = BinaryCodec(
        "protobuf",
        'syntax = "proto3"; message E { int64 id = 1; repeated string tags = 2; }',
        message="E",
    )
    with pytest.raises(ValueError):
        c.encode(row)
    assert c.decode(c.encode({"id": 0, "tags": []})) == {"id": 0, "tags": []}


def test_registry_url_rejection_never_echoes_credentials_for_unknown_schemes():
    from shape_kafka.registry import RegistryClient

    with pytest.raises(ShapeError) as caught:
        RegistryClient("ftp://user:private-password@registry.test")
    assert "private-password" not in str(caught.value)


@pytest.mark.parametrize(
    "arrow,kind,maximum",
    [(pa.uint8(), "int", 255), (pa.uint16(), "int", 65535), (pa.uint32(), "long", 2**32 - 1)],
)
def test_local_avro_safe_unsigned_mappings_include_nested_boundaries(arrow, kind, maximum):
    from shape.streaming.binary import BinaryCodec

    text = json.dumps(
        {
            "type": "record",
            "name": "E",
            "fields": [
                {"name": "id", "type": kind},
                {
                    "name": "child",
                    "type": {
                        "type": "record",
                        "name": "C",
                        "fields": [{"name": "n", "type": kind}],
                    },
                },
                {"name": "items", "type": {"type": "array", "items": kind}},
                {"name": "attrs", "type": {"type": "map", "values": kind}},
            ],
        }
    )
    c = BinaryCodec("avro", text)
    schema = pa.schema(
        [
            ("id", arrow),
            ("child", pa.struct([("n", arrow)])),
            ("items", pa.list_(arrow)),
            ("attrs", pa.map_(pa.string(), arrow)),
        ]
    )
    c.validate_schema(schema)
    for value in [0, maximum]:
        row = {"id": value, "child": {"n": value}, "items": [value], "attrs": {"x": value}}
        assert c.decode(c.encode(row)) == row


@pytest.mark.parametrize(
    "actual,kind", [(pa.uint32(), "int"), (pa.uint64(), "long"), (pa.int64(), "int")]
)
def test_local_avro_refuses_unsafe_integer_widths(actual, kind):
    from shape.streaming.binary import BinaryCodec

    c = BinaryCodec(
        "avro",
        json.dumps({"type": "record", "name": "E", "fields": [{"name": "id", "type": kind}]}),
    )
    with pytest.raises(ShapeError, match="id"):
        c.validate_schema(pa.schema([("id", actual)]))


def test_avro_long_overflow_is_refused_at_wire_boundary():
    from shape.streaming.binary import BinaryCodec

    c = BinaryCodec("avro", '{"type":"record","name":"E","fields":[{"name":"id","type":"long"}]}')
    assert c.decode(c.encode({"id": 2**63 - 1})) == {"id": 2**63 - 1}
    assert c.decode(c.encode({"id": -(2**63)})) == {"id": -(2**63)}
    with pytest.raises(ValueError):
        c.encode({"id": 2**63})
    with pytest.raises(ValueError):
        c.encode({"id": -(2**63) - 1})


@pytest.mark.parametrize("fmt", ["avro", "protobuf"])
def test_local_uuid_wire_strings_keep_nested_types_on_read_and_write(fmt):
    import uuid

    from shape.streaming.binary import BinaryCodec

    class UUIDType(pa.ExtensionType):
        def __init__(self):
            super().__init__(pa.binary(16), "arrow.uuid")

        def __arrow_ext_serialize__(self):
            return b""

        @classmethod
        def __arrow_ext_deserialize__(cls, storage_type, serialized):
            return cls()

    arrow = pa.uuid() if hasattr(pa, "uuid") else UUIDType()
    item = {"type": "string", "logicalType": "uuid"}
    text = (
        json.dumps(
            {
                "type": "record",
                "name": "E",
                "fields": [
                    {"name": "id", "type": item},
                    {
                        "name": "child",
                        "type": {
                            "type": "record",
                            "name": "C",
                            "fields": [{"name": "n", "type": item}],
                        },
                    },
                    {"name": "items", "type": {"type": "array", "items": item}},
                    {"name": "attrs", "type": {"type": "map", "values": item}},
                ],
            }
        )
        if fmt == "avro"
        else """syntax = "proto3";
    message E {
      string id = 1; C child = 2; repeated string items = 3; map<string,string> attrs = 4;
    }
    message C { string n = 1; }
    """
    )
    c = BinaryCodec(fmt, text, message="E")
    schema = pa.schema(
        [
            ("id", arrow),
            ("child", pa.struct([("n", arrow)])),
            ("items", pa.list_(arrow)),
            ("attrs", pa.map_(pa.string(), arrow)),
        ]
    )
    c.validate_schema(schema)
    with pytest.raises(ShapeError, match="id"):
        c.validate_schema(schema.set(0, pa.field("id", pa.binary(16))))
    value = uuid.UUID(int=7)
    row = {"id": value, "child": {"n": value}, "items": [value], "attrs": {"x": value}}
    body = c.encode(row)
    expected = {
        "id": str(value),
        "child": {"n": str(value)},
        "items": [str(value)],
        "attrs": {"x": str(value)},
    }
    assert c.decode(body) == expected
    batch = decode_messages([StreamMessage("0", 0, body)], decoder=c.decode)
    assert batch.num_rows == 1
    assert batch.schema.field("id").type == pa.string()
    assert pa.types.is_struct(batch.schema.field("child").type)
    assert pa.types.is_list(batch.schema.field("items").type)
    assert pa.types.is_map(batch.schema.field("attrs").type)


def test_protobuf_integer_mapping_keeps_unsafe_signedness_refusals():
    from shape.streaming.binary import BinaryCodec

    signed = BinaryCodec("protobuf", 'syntax = "proto3"; message E { int64 id = 1; }', message="E")
    signed.validate_schema(pa.schema([("id", pa.uint32())]))
    assert signed.decode(signed.encode({"id": 2**32 - 1})) == {"id": 2**32 - 1}
    unsigned = BinaryCodec(
        "protobuf", 'syntax = "proto3"; message E { uint32 id = 1; }', message="E"
    )
    with pytest.raises(ShapeError, match="id"):
        unsigned.validate_schema(pa.schema([("id", pa.int32())]))
    with pytest.raises(ValueError):
        unsigned.encode({"id": -1})


def test_avro_uuid_normalization_does_not_change_nonfinite_read_values():
    import math

    from shape.streaming.binary import BinaryCodec

    text = '{"type":"record","name":"E","fields":[{"name":"value","type":"double"}]}'
    buf = io.BytesIO()
    fastavro.schemaless_writer(buf, json.loads(text), {"value": float("nan")})
    c = BinaryCodec("avro", text)
    assert math.isnan(c.decode(buf.getvalue())["value"])
