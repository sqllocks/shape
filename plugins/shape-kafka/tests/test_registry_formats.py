"""Schema registry formats for kafka:// (W2-09 item 1): Avro, Protobuf and JSON Schema.

Everything runs against the in-process fake registry (no network). Each message is decoded back
from the registered schema alone and compared, value for value, with the flat event.
"""

import base64
import datetime as dt
import decimal
import json
import struct
import sys

import pyarrow as pa
import pytest
from shape_kafka import KafkaEmitter
from shape_kafka.formats import EVENT_FORMATS, columns_of, make_codec
from shape_kafka.registry import RegistryClient, check_url, subject_name
from shape_kafka.testing import EmitterHarness, FakeRegistry, decode_messages

from shape.errors import ShapeError
from shape.streaming.emit import EmitConfig, EmitRunner, RejectedEvents
from shape.streaming.emit.formats import FIELD_POISON, encode_events, rows_of, with_event_fields
from shape.streaming.emit.sinks import EmitterSink

pytestmark = pytest.mark.contract

REGISTRY_URL = "http://registry.test:8081"
BINARY_FORMATS = ("avro", "protobuf")
ALL_FORMATS = ("avro", "protobuf", "json-schema")


def typed_batch(n: int = 40) -> pa.RecordBatch:
    """Every type the mapping documents, with nulls, as events of the table ``typed``."""
    D = decimal.Decimal
    t0 = dt.datetime(2024, 3, 9, 12, 30, 15, 123456)
    cols = {
        "born": pa.array([dt.date(1999, 12, 31) + dt.timedelta(days=i * 37) for i in range(n)]),
        "id": pa.array(range(n), pa.int64()),
        "tiny": pa.array([(i % 200) - 100 for i in range(n)], pa.int8()),
        "short": pa.array([i * 100 - 2000 for i in range(n)], pa.int16()),
        "mid": pa.array([i * 1_000_000 - 9_000_000 for i in range(n)], pa.int32()),
        "u8": pa.array([i % 256 for i in range(n)], pa.uint8()),
        "u16": pa.array([i * 1000 % 65536 for i in range(n)], pa.uint16()),
        "u32": pa.array([4_000_000_000 - i for i in range(n)], pa.uint32()),
        "u64": pa.array([(1 << 62) + i for i in range(n)], pa.uint64()),
        "flag": pa.array([None if i % 7 == 0 else bool(i % 2) for i in range(n)]),
        "f32": pa.array([None if i % 5 == 0 else i * 0.25 for i in range(n)], pa.float32()),
        "f64": pa.array(
            [float("nan"), float("inf"), -float("inf"), None, 1.5e300, -0.0, 3.14159, 2.0]
            * (n // 8)
            + [1.0] * (n % 8),
            pa.float64(),
        ),
        "name": pa.array([None if i % 4 == 0 else f'name é"{i}"\n' for i in range(n)]),
        "uid": pa.array([f"00000000-0000-4000-8000-{i:012d}" for i in range(n)]),
        "blob": pa.array([None if i % 6 == 0 else bytes([i, 0, 255]) for i in range(n)]),
        "amount": pa.array(
            [None if i % 9 == 0 else D(i) / 4 - D("1000.25") for i in range(n)],
            pa.decimal128(12, 2),
        ),
        "tiny_dec": pa.array([D(i) / D(10**7) for i in range(n)], pa.decimal128(18, 8)),
        "at": pa.array(
            [dt.time(i % 24, i % 60, (i * 7) % 60, i) for i in range(n)], pa.time64("us")
        ),
        "ts_naive": pa.array([t0 + dt.timedelta(seconds=i, microseconds=i) for i in range(n)]),
        "ts_ms": pa.array(
            [t0.replace(microsecond=123000) + dt.timedelta(minutes=i) for i in range(n)],
            pa.timestamp("ms"),
        ),
        "ts_s": pa.array(
            [t0.replace(microsecond=0) + dt.timedelta(hours=i) for i in range(n)], pa.timestamp("s")
        ),
        "ts_utc": pa.array(
            [t0.replace(tzinfo=dt.UTC) + dt.timedelta(days=i) for i in range(n)],
            pa.timestamp("us", "UTC"),
        ),
        "ts_zone": pa.array(
            [t0.replace(tzinfo=dt.UTC) + dt.timedelta(days=i) for i in range(n)],
            pa.timestamp("ms", "Europe/Paris"),
        ),
    }
    batch = pa.RecordBatch.from_arrays(list(cols.values()), names=list(cols))
    return with_event_fields(batch, "typed", 1000)


def _wires(registry, fmt, batch, topic="events", **opts):
    """Emit ``batch`` as ``fmt`` and return what the producer was handed."""
    h = EmitterHarness(topic, registry)
    h.make().emit(
        h.uri,
        [batch],
        event_format=fmt,
        schema_registry_url=REGISTRY_URL,
        **opts,
    )
    return h


@pytest.mark.parametrize("fmt", ALL_FORMATS)
def test_every_message_decodes_back_to_the_flat_event_value_for_value(fmt):
    batch = typed_batch()
    registry = FakeRegistry()
    h = _wires(registry, fmt, batch)
    values = [v for _, _, v, _ in h.store.log]
    assert len(values) == batch.num_rows
    assert decode_messages(registry, values, batch.schema) == rows_of(batch)
    # the flat json event is what json sends: the same dicts
    assert rows_of(batch) == [json.loads(e.body) for e in encode_events(batch)]


@pytest.mark.parametrize("fmt", ALL_FORMATS)
def test_wire_format_is_magic_zero_big_endian_schema_id_then_payload(fmt):
    registry = FakeRegistry()
    h = _wires(registry, fmt, typed_batch(3))
    (sid,) = registry.schemas  # one table, one schema
    for _, _, value, _ in h.store.log:
        assert value[0] == 0
        assert value[1:5] == struct.pack(">I", sid) and sid == 100
    value = h.store.log[0][2]
    if fmt == "protobuf":
        assert value[5] == 0  # the message-index list [0]
    if fmt == "json-schema":
        assert json.loads(value[5:])["_shape_table"] == "typed"


@pytest.mark.parametrize("fmt", ALL_FORMATS)
def test_the_message_key_stays_table_slash_seq_and_the_table_header_is_kept(fmt):
    batch = typed_batch(5)
    h = _wires(FakeRegistry(), fmt, batch, synthetic=True)
    assert [k for _, k, _, _ in h.store.log] == [f"typed/{1000 + i}".encode() for i in range(5)]
    assert all(
        hd == [("shape-table", b"typed"), ("shape-synthetic", b"true")] for *_, hd in h.store.log
    )


def test_json_is_the_default_and_its_bytes_are_unchanged():
    batch = typed_batch(5)
    for opts in ({}, {"event_format": "json"}):
        h = EmitterHarness()
        h.make().emit(h.uri, [batch], **opts)
        assert [v for _, _, v, _ in h.store.log] == [e.body for e in encode_events(batch)]
        assert h.registry.requests == []  # json never talks to a registry


# ---- the documented type mapping ------------------------------------------------------------


def _avro(batch):
    cols = columns_of("typed", batch.schema, ident=True)
    return json.loads(make_codec("avro", "typed", batch.schema).schema_text), cols


def test_avro_type_mapping():
    doc, _ = _avro(typed_batch(2))
    assert doc["type"] == "record" and doc["name"] == "typed" and doc["namespace"] == "shape.events"
    t = {f["name"]: f["type"] for f in doc["fields"]}
    null = "null"
    assert t["id"] == [null, "long"] and t["tiny"] == [null, "int"] and t["short"] == [null, "int"]
    assert t["mid"] == [null, "int"] and t["u8"] == [null, "int"] and t["u16"] == [null, "int"]
    assert t["u32"] == [null, "long"] and t["u64"] == [null, "long"]
    assert t["flag"] == [null, "boolean"]
    assert t["f32"] == [null, "float"] and t["f64"] == [null, "double"]
    assert t["name"] == [null, "string"] and t["blob"] == [null, "bytes"]
    assert t["uid"] == [null, "string"]  # a plain string column is a string
    assert t["amount"] == [
        null,
        {"type": "bytes", "logicalType": "decimal", "precision": 12, "scale": 2},
    ]
    assert t["born"] == [null, {"type": "int", "logicalType": "date"}]
    assert t["at"] == [null, {"type": "long", "logicalType": "time-micros"}]
    assert t["ts_naive"] == [null, {"type": "long", "logicalType": "local-timestamp-micros"}]
    assert t["ts_ms"] == [null, {"type": "long", "logicalType": "local-timestamp-millis"}]
    assert t["ts_s"] == [null, {"type": "long", "logicalType": "local-timestamp-millis"}]
    assert t["ts_utc"] == [null, {"type": "long", "logicalType": "timestamp-micros"}]
    assert t["ts_zone"] == [null, {"type": "long", "logicalType": "timestamp-millis"}]
    # the event fields: the key parts are never null, the event time follows the date column
    assert t["_shape_table"] == "string" and t["_shape_seq"] == "long"
    assert t["_shape_event_time"] == [null, {"type": "int", "logicalType": "date"}]
    assert all(f["default"] is None for f in doc["fields"] if isinstance(f["type"], list))


def test_a_not_null_column_is_not_a_union_but_a_float_always_is():
    schema = pa.schema(
        [
            pa.field("a", pa.int64(), nullable=False),
            pa.field("f", pa.float64(), nullable=False),
            pa.field("_shape_table", pa.string()),
            pa.field("_shape_seq", pa.int64()),
        ]
    )
    doc = json.loads(make_codec("avro", "t", schema).schema_text)
    t = {f["name"]: f["type"] for f in doc["fields"]}
    assert t["a"] == "long" and t["f"] == ["null", "double"]
    assert t["_shape_table"] == "string" and t["_shape_seq"] == "long"
    js = json.loads(make_codec("json-schema", "t", schema).schema_text)
    assert js["required"] == ["a", "_shape_table", "_shape_seq"]
    proto = make_codec("protobuf", "t", schema).schema_text
    assert "  int64 a = 1;" in proto and "  optional double f = 2;" in proto


def test_protobuf_type_mapping():
    text = make_codec("protobuf", "typed", typed_batch(2).schema).schema_text
    lines = {ln.split("=")[0].split()[-1]: ln for ln in text.splitlines() if " = " in ln}
    assert text.startswith('syntax = "proto3";\n\npackage shape.events;\n\nmessage typed {')
    expect = {
        "id": "optional int64",
        "tiny": "optional int32",
        "short": "optional int32",
        "mid": "optional int32",
        "u8": "optional uint32",
        "u16": "optional uint32",
        "u32": "optional uint32",
        "u64": "optional uint64",
        "flag": "optional bool",
        "f32": "optional float",
        "f64": "optional double",
        "name": "optional string",
        "uid": "optional string",
        "blob": "optional bytes",
        "amount": "optional string",
        "born": "optional int32",
        "at": "optional int64",
        "ts_naive": "optional int64",
        "ts_utc": "optional int64",
        "_shape_table": "string",
        "_shape_seq": "int64",
    }
    for name, typ in expect.items():
        assert lines[name].strip().startswith(f"{typ} {name} = "), lines[name]
    assert "timestamp without time zone" in lines["ts_naive"]
    assert "timestamp with time zone" in lines["ts_utc"]
    assert "decimal(12, 2)" in lines["amount"]


def test_json_schema_type_mapping():
    doc = json.loads(make_codec("json-schema", "typed", typed_batch(2).schema).schema_text)
    assert doc["$schema"].endswith("draft-07/schema#") and doc["title"] == "typed"
    assert doc["additionalProperties"] is False
    p = doc["properties"]
    assert p["id"]["type"] == ["integer", "null"] and p["flag"]["type"] == ["boolean", "null"]
    assert p["f64"]["type"] == ["number", "null"]
    assert p["blob"] == {"type": ["string", "null"], "contentEncoding": "base64"}
    assert p["born"] == {"type": ["string", "null"], "format": "date"}
    assert p["ts_utc"]["format"] == "date-time" and "format" not in p["ts_naive"]
    assert p["ts_naive"]["description"] == "timestamp without time zone"
    assert "decimal(12, 2)" in p["amount"]["description"]
    assert p["_shape_table"] == {"type": "string"} and "_shape_seq" in doc["required"]


@pytest.mark.parametrize("fmt", ALL_FORMATS)
def test_a_dictionary_column_is_its_value_type(fmt):
    batch = typed_batch(10)
    i = batch.schema.get_field_index("name")
    dict_batch = batch.set_column(i, "name", batch.column(i).dictionary_encode())
    registry = FakeRegistry()
    h = _wires(registry, fmt, dict_batch)
    values = [v for _, _, v, _ in h.store.log]
    assert decode_messages(registry, values, dict_batch.schema) == rows_of(dict_batch)


def test_nanosecond_timestamps_are_cut_to_microseconds_in_avro_and_protobuf():
    ns = pa.array([1_700_000_000_123_456_789], pa.timestamp("ns"))
    batch = with_event_fields(pa.RecordBatch.from_arrays([ns], names=["t"]), "n", 0)
    for fmt in BINARY_FORMATS:
        registry = FakeRegistry()
        h = _wires(registry, fmt, batch)
        (event,) = decode_messages(registry, [h.store.log[0][2]], batch.schema)
        assert event["t"] == "2023-11-14T22:13:20.123456000"  # the last three digits are gone


def test_uuid_extension_type_is_a_string_with_the_uuid_logical_type():
    import uuid

    if not hasattr(pa, "uuid"):
        pytest.fail("this test needs pyarrow with the uuid extension type")
    u = pa.array([uuid.UUID(int=i) for i in range(3)], pa.uuid())
    batch = with_event_fields(pa.RecordBatch.from_arrays([u], names=["u"]), "g", 0)
    doc = json.loads(make_codec("avro", "g", batch.schema).schema_text)
    assert doc["fields"][0]["type"] == ["null", {"type": "string", "logicalType": "uuid"}]
    js = json.loads(make_codec("json-schema", "g", batch.schema).schema_text)
    assert js["properties"]["u"]["format"] == "uuid"
    for fmt in ALL_FORMATS:
        registry = FakeRegistry()
        h = _wires(registry, fmt, batch)
        events = decode_messages(registry, [v for _, _, v, _ in h.store.log], batch.schema)
        assert [str(e["u"]) for e in events] == [str(uuid.UUID(int=i)) for i in range(3)]


def test_types_that_have_no_mapping_are_refused_naming_the_column():
    lst = pa.array([[1], [2]], pa.list_(pa.int64()))
    batch = with_event_fields(pa.RecordBatch.from_arrays([lst], names=["tags"]), "t", 0)
    for fmt in ALL_FORMATS:
        with pytest.raises(ShapeError, match="cannot map column 'tags'"):
            make_codec(fmt, "t", batch.schema)


@pytest.mark.parametrize("fmt", BINARY_FORMATS)
def test_invalid_identifiers_are_refused_for_avro_and_protobuf(fmt):
    batch = with_event_fields(
        pa.RecordBatch.from_arrays([pa.array([1])], names=["order id"]), "t", 0
    )
    with pytest.raises(ShapeError, match="column 'order id'.*not a valid field name"):
        make_codec(fmt, "t", batch.schema)
    batch = with_event_fields(
        pa.RecordBatch.from_arrays([pa.array([1])], names=["a"]), "my-table", 0
    )
    with pytest.raises(ShapeError, match="table name 'my-table'"):
        make_codec(fmt, "my-table", batch.schema)
    # json schema takes any name
    make_codec("json-schema", "my-table", batch.schema)


# ---- the registry: subjects, credentials, refusals --------------------------------------------


def test_subject_naming_strategies():
    assert subject_name("topic", "orders", "shape.events.order_line") == "orders-value"
    assert subject_name("record", "orders", "shape.events.order_line") == "shape.events.order_line"
    assert (
        subject_name("topic_record", "orders", "shape.events.order_line")
        == "orders-shape.events.order_line"
    )
    with pytest.raises(ShapeError, match="unknown subject strategy 'nope'"):
        subject_name("nope", "orders", "r")


@pytest.mark.parametrize(
    ("strategy", "subject"),
    [
        ("topic", "orders-value"),
        ("record", "shape.events.typed"),
        ("topic_record", "orders-shape.events.typed"),
    ],
)
@pytest.mark.parametrize("fmt", ALL_FORMATS)
def test_the_subject_the_schema_is_registered_under(fmt, strategy, subject):
    registry = FakeRegistry()
    _wires(registry, fmt, typed_batch(3), topic="orders", subject_strategy=strategy)
    assert list(registry.subjects) == [subject]
    kinds = {"avro": "AVRO", "protobuf": "PROTOBUF", "json-schema": "JSON"}
    assert next(iter(registry.schemas.values()))[1] == kinds[fmt]


def test_a_schema_is_registered_once_per_table_per_run_and_reused_by_id():
    registry = FakeRegistry()
    h = EmitterHarness("orders", registry)
    e = h.make()
    kw = {"event_format": "avro", "schema_registry_url": REGISTRY_URL}
    for _ in range(3):
        e.emit(h.uri, [typed_batch(4)], **kw)
    posts = [r for r in registry.requests if r[0] == "POST"]
    assert len(posts) == 1 and posts[0][1] == f"{REGISTRY_URL}/subjects/orders-value/versions"
    assert len({v[1:5] for _, _, v, _ in h.store.log}) == 1
    # a second process registers again and gets the same id (the registry is idempotent)
    h2 = EmitterHarness("orders", registry)
    h2.make().emit(h2.uri, [typed_batch(4)], **kw)
    assert h2.store.log[0][2][:5] == h.store.log[0][2][:5]


def test_two_tables_on_one_topic_need_a_record_strategy():
    a = typed_batch(2)
    b = with_event_fields(pa.RecordBatch.from_arrays([pa.array([1, 2])], names=["x"]), "other", 0)
    registry = FakeRegistry()
    h = EmitterHarness("orders", registry)
    e = h.make()
    kw = {"event_format": "avro", "schema_registry_url": REGISTRY_URL}
    e.emit(h.uri, [a], subject_strategy="topic_record", **kw)
    e.emit(h.uri, [b], subject_strategy="topic_record", **kw)
    assert sorted(registry.subjects) == ["orders-shape.events.other", "orders-shape.events.typed"]
    # the topic strategy puts both under one subject: the registry's compatibility rule refuses
    h = EmitterHarness("orders", FakeRegistry())
    e = h.make()
    e.emit(h.uri, [a], **kw)
    with pytest.raises(
        ShapeError, match=r"refused the schema for subject orders-value: .*incompatible"
    ):
        e.emit(h.uri, [b], **kw)


def test_a_registry_that_refuses_the_schema_stops_with_the_documented_message():
    registry = FakeRegistry(refuse={"events-value": "Invalid schema: bad field"})
    with pytest.raises(ShapeError) as err:
        _wires(registry, "avro", typed_batch(3))
    assert str(err.value) == (
        "schema registry refused the schema for subject events-value: Invalid schema: bad field"
    )


def test_a_refusal_is_never_retried_but_an_unreachable_registry_is_a_connection_error():
    registry = FakeRegistry(down=True)
    with pytest.raises(ConnectionError, match="schema registry error 503"):
        _wires(registry, "avro", typed_batch(3))

    def refuse(*_a):
        raise ConnectionError("schema registry unreachable: [Errno 111]")

    h = EmitterHarness()
    with pytest.raises(ConnectionError, match="unreachable"):
        KafkaEmitter(h.store.factory, registry_transport=refuse).emit(
            h.uri, [typed_batch(2)], event_format="avro", schema_registry_url=REGISTRY_URL
        )
    assert h.store.log == []  # nothing is sent without a schema id


def test_registry_credentials_are_basic_auth_from_resolved_values():
    registry = FakeRegistry(auth=("svc", "s3cret"))
    h = _wires(
        registry,
        "avro",
        typed_batch(2),
        schema_registry_username="svc",
        schema_registry_password="s3cret",
    )
    assert h.store.log
    token = base64.b64encode(b"svc:s3cret").decode()
    assert registry.requests[0][3]["Authorization"] == f"Basic {token}"
    with pytest.raises(
        ShapeError, match="refused the schema for subject events-value: Unauthorized"
    ):
        _wires(registry, "avro", typed_batch(2))  # no credentials
    with pytest.raises(ShapeError, match="Unauthorized"):
        _wires(
            registry,
            "avro",
            typed_batch(2),
            schema_registry_username="svc",
            schema_registry_password="wrong",
        )


def test_a_url_with_credentials_or_half_a_login_is_refused():
    with pytest.raises(ShapeError, match="must not carry credentials"):
        check_url("https://user:pw@registry.test")
    with pytest.raises(ShapeError, match="must not carry credentials"):
        check_url("https://user@registry.test")
    with pytest.raises(ShapeError, match="not an http"):
        check_url("registry.test:8081")
    with pytest.raises(ShapeError, match="both"):
        RegistryClient(REGISTRY_URL, username="svc")
    with pytest.raises(ShapeError, match="both"):
        RegistryClient(REGISTRY_URL, password="x")
    assert check_url("http://r:8081/") == "http://r:8081"


def test_option_errors():
    h = EmitterHarness()
    e = h.make()
    batch = typed_batch(2)
    with pytest.raises(
        ShapeError, match="needs a schema registry: --sink-config kafka.schema_registry_url"
    ):
        e.emit(h.uri, [batch], event_format="avro")
    with pytest.raises(ShapeError, match="unknown event format 'xml'"):
        e.emit(h.uri, [batch], event_format="xml")
    with pytest.raises(ShapeError, match="JSON only"):
        e.emit(
            h.uri,
            [batch],
            event_format="protobuf",
            envelope="cloudevents",
            schema_registry_url=REGISTRY_URL,
        )
    with pytest.raises(ShapeError, match="unknown subject strategy"):
        e.emit(
            h.uri,
            [batch],
            event_format="avro",
            subject_strategy="x",
            schema_registry_url=REGISTRY_URL,
        )
    assert h.store.log == [] and h.registry.requests == []
    assert set(EVENT_FORMATS) == {"json", "avro", "protobuf", "json-schema"}


@pytest.mark.parametrize(
    ("fmt", "module", "extra"),
    [
        ("avro", "fastavro", "avro"),
        ("protobuf", "google.protobuf", "protobuf"),
    ],
)
def test_a_missing_extra_names_the_pip_command(monkeypatch, fmt, module, extra):
    monkeypatch.setitem(sys.modules, module, None)
    with pytest.raises(ShapeError) as err:
        _wires(FakeRegistry(), fmt, typed_batch(2))
    assert f"pip install 'sqllocks-shape-kafka[{extra}]'" in str(err.value)
    assert f"--event-format {fmt}" in str(err.value)


def test_json_schema_needs_no_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "fastavro", None)
    monkeypatch.setitem(sys.modules, "google.protobuf", None)
    h = _wires(FakeRegistry(), "json-schema", typed_batch(2))
    assert len(h.store.log) == 2


# ---- encoding limits and poison --------------------------------------------------------------


def test_a_value_that_does_not_fit_the_format_is_a_rejection_naming_the_event():
    big = pa.array([(1 << 63) + 5], pa.uint64())
    batch = with_event_fields(pa.RecordBatch.from_arrays([big], names=["n"]), "t", 7)
    with pytest.raises(RejectedEvents) as err:
        _wires(FakeRegistry(), "avro", batch)
    assert err.value.keys == ["t/7"]
    assert err.value.reasons == ["cannot encode as avro: column 'n' does not fit a long"]
    # protobuf has a uint64
    registry = FakeRegistry()
    h = _wires(registry, "protobuf", batch)
    assert decode_messages(registry, [h.store.log[0][2]], batch.schema)[0]["n"] == (1 << 63) + 5


def test_poison_events_are_cut_off_in_every_format():
    batch = typed_batch(6).append_column(FIELD_POISON, pa.array([False, True] * 3))
    for fmt in ALL_FORMATS:
        registry = FakeRegistry()
        h = _wires(registry, fmt, batch)
        values = [v for _, _, v, _ in h.store.log]
        assert len(values) == 6
        clean = [values[i] for i in (0, 2, 4)]
        assert decode_messages(registry, clean, batch.schema) == [
            rows_of(typed_batch(6))[i] for i in (0, 2, 4)
        ]
        for i in (1, 3, 5):
            with pytest.raises((ValueError, KeyError, EOFError, Exception)):
                decode_messages(registry, [values[i]], batch.schema)
            assert len(values[i]) < len(h.store.log[0][2]) * 2  # cut, never longer


# ---- the run, the checkpoint and the command line --------------------------------------------


@pytest.mark.parametrize("fmt", ALL_FORMATS)
def test_a_run_delivers_the_runtimes_events_in_the_format(fmt):
    from shape.streaming.emit import contract

    plan = contract.default_plan()
    registry = FakeRegistry()
    h = EmitterHarness("orders", registry)
    sink = EmitterSink(
        h.make(),
        h.uri,
        event_format=fmt,
        schema_registry_url=REGISTRY_URL,
        subject_strategy="record",
    )
    report = EmitRunner(plan, sink, EmitConfig(max_events=1500, batch_events=400)).run()
    assert report.events == 1500
    ref = contract.reference(contract.default_plan())[:1500]
    first = next(iter(plan.blocks(0))).batch
    got = decode_messages(registry, [v for _, _, v, _ in h.store.log], first.schema)
    assert got == ref
    assert [k.decode() for _, k, _, _ in h.store.log] == [f"order_line/{i}" for i in range(1500)]
    assert list(registry.subjects) == ["shape.events.order_line"]


def test_retried_batches_repeat_messages_with_the_same_schema_id():
    from shape.streaming.emit import contract

    registry = FakeRegistry()
    h = EmitterHarness("orders", registry)
    h.inject_failures(1)
    sink = EmitterSink(h.make(), h.uri, event_format="avro", schema_registry_url=REGISTRY_URL)
    report = EmitRunner(
        contract.default_plan(), sink, EmitConfig(max_events=800, batch_events=400, retry_backoff=0)
    ).run()
    assert report.retries >= 1 and report.complete
    ids = {v[1:5] for _, _, v, _ in h.store.log}
    assert len(ids) == 1
    keys = [k for _, k, _, _ in h.store.log]
    assert len(keys) > 800 and len(set(keys)) == 800


def _main_with(monkeypatch, harness):
    from shape.plugins.host import PluginHost
    from shape.plugins.registry import register_builtins

    host = PluginHost(entry_points=lambda: [])
    register_builtins(host)
    host.register("shape.emitters", "kafka", harness.make, api="1.0", source="test")
    monkeypatch.setattr("shape.plugins.host.default_host", lambda: host)


def test_shape_emit_with_an_event_format_and_sink_config(monkeypatch, capsys):
    from shape.cli.main import main

    h = EmitterHarness("orders")
    _main_with(monkeypatch, h)
    monkeypatch.setenv("REG_PW", "s3cret")
    h.registry.auth = ("svc", "s3cret")
    argv = [
        "emit", "retail", "--table", "order_line", "--max-events", "600",
        "--sink", h.uri, "--event-format", "avro",
        "--sink-config", f"kafka.schema_registry_url={REGISTRY_URL}",
        "--sink-config", "kafka.subject_strategy=topic_record",
        "--sink-config", "kafka.schema_registry_username=svc",
        "--sink-config", "kafka.schema_registry_password=env://REG_PW",
    ]  # fmt: skip
    assert main([*argv, "--json"]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["events"] == 600
    assert len(h.store.log) == 600 and h.store.log[0][2][0] == 0
    assert list(h.registry.subjects) == ["orders-shape.events.order_line"]


def test_shape_emit_stops_with_exit_2_when_the_registry_refuses(monkeypatch, capsys):
    from shape.cli.main import main

    h = EmitterHarness("orders", FakeRegistry(refuse={"orders-value": "no thanks"}))
    _main_with(monkeypatch, h)
    code = main(
        [
            "emit", "retail", "--table", "order_line", "--max-events", "10", "--sink", h.uri,
            "--event-format", "protobuf",
            "--sink-config", f"kafka.schema_registry_url={REGISTRY_URL}",
        ]
    )  # fmt: skip
    assert code == 2
    assert "schema registry refused the schema for subject orders-value: no thanks" in (
        capsys.readouterr().err
    )
    assert h.store.log == []


def test_a_literal_registry_password_is_refused(monkeypatch, capsys):
    from shape.cli.main import main

    h = EmitterHarness("orders")
    _main_with(monkeypatch, h)
    code = main(
        [
            "emit", "retail", "--table", "order_line", "--max-events", "10", "--sink", h.uri,
            "--event-format", "avro",
            "--sink-config", f"kafka.schema_registry_url={REGISTRY_URL}",
            "--sink-config", "kafka.schema_registry_username=svc",
            "--sink-config", "kafka.schema_registry_password=hunter2",
        ]
    )  # fmt: skip
    assert code == 2
    assert "hunter2" not in capsys.readouterr().err
    assert h.registry.requests == []


@pytest.mark.parametrize("fmt", ALL_FORMATS)
def test_a_table_whose_columns_change_during_the_run_registers_a_new_schema(fmt):
    """A drift plan that adds a column: the new schema goes to the registry under the same
    subject, the messages carry the new id, and each decodes against its own schema."""
    first = typed_batch(4)
    j = first.schema.get_field_index("name")
    second = first.append_column("channel", pa.array(["web"] * 4))
    dropped = first.remove_column(j)
    registry = FakeRegistry(evolve=True)
    h = EmitterHarness("orders", registry)
    e = h.make()
    kw = {"event_format": fmt, "schema_registry_url": REGISTRY_URL, "subject_strategy": "record"}
    for b in (first, second, dropped):
        e.emit(h.uri, [b], **kw)
    ids = [struct.unpack(">I", v[1:5])[0] for _, _, v, _ in h.store.log]
    assert ids == [100] * 4 + [101] * 4 + [102] * 4
    assert len(registry.subjects) == 1  # one subject, three versions
    for part, b in zip(
        (slice(0, 4), slice(4, 8), slice(8, 12)), (first, second, dropped), strict=True
    ):
        values = [v for _, _, v, _ in h.store.log][part]
        assert decode_messages(registry, values, b.schema) == rows_of(b)
    # a registry that does not allow the change refuses the new schema, as it does anything else
    strict = EmitterHarness("orders", FakeRegistry())
    e = strict.make()
    e.emit(strict.uri, [first], **kw)
    with pytest.raises(ShapeError, match="refused the schema for subject"):
        e.emit(strict.uri, [second], **kw)
