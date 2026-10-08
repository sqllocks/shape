"""W9-08 message metadata acceptance (offline)."""

from datetime import UTC, datetime

import pyarrow as pa
import pytest
from shape_kafka.testing import EmitterHarness as KafkaHarness

from shape.errors import ShapeError
from shape.streaming.emit.formats import with_event_fields


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


@pytest.mark.parametrize("harness", [KafkaHarness])
def test_wanted1_column_keys_and_static_column_headers(harness):
    h = harness()
    h.make().emit(
        h.uri, [batch()], key="customer|region", headers=["trace=static", "customer=@customer"]
    )
    if True:
        assert [x[1] for x in h.store.log] == [b"a|1", b"b|2"]
        assert ("shape-key", b"orders/0") in h.store.log[0][3]
        assert ("customer", b"a") in h.store.log[0][3]
    else:
        e = h.hub.batches[0].events[0]
        assert e.properties["shape-key"] == "orders/0"
        assert e.properties["customer"] == b"a"


@pytest.mark.parametrize("harness", [KafkaHarness])
@pytest.mark.parametrize(
    "option", [{"key": "missing"}, {"headers": ["x=@missing"]}, {"timestamp": "invalid"}]
)
def test_wanted1_bad_options_refused_before_send(harness, option):
    h = harness()
    with pytest.raises(ShapeError):
        h.make().emit(h.uri, [batch()], **option)
    assert h.delivered() == []


@pytest.mark.parametrize("harness", [KafkaHarness])
@pytest.mark.parametrize(
    "header", ["shape-table=x", "shape-key=x", "shape-synthetic=x", "shape-dead-letter-reason=x"]
)
def test_wanted2_reserved_headers_cannot_be_shadowed(harness, header):
    h = harness()
    with pytest.raises(ShapeError, match="reserved"):
        h.make().emit(h.uri, [batch()], headers=[header], synthetic=True)
    assert h.delivered() == []


def test_wanted1_kafka_partition_timestamp():
    from shape_kafka import KafkaEmitter

    calls = []

    class Producer:
        def produce(self, *args, **kwargs):
            calls.append(kwargs)

        def poll(self, *args):
            pass

        def flush(self, *args):
            return 0

    KafkaEmitter(lambda _: Producer()).emit(
        "kafka://b/t", [batch()], partition=0, timestamp="event_time"
    )
    assert calls[0]["partition"] == 0
    assert calls[0]["timestamp"] == 1767225600000
    with pytest.raises(ShapeError):
        KafkaEmitter(lambda _: Producer()).emit("kafka://b/t", [batch()], partition=-1)


def test_wanted3_kafka_source_metadata_and_schema_across_batches():
    from shape_kafka.testing import FakeBroker

    broker = FakeBroker(
        {"events": {0: [(b'{"x":1}', 12, b"a", [("h", b"\xff")]), (b'{"x":2}', 13, b"b", [])]}}
    )
    batches = list(
        broker.source().read(
            "kafka://b/events", batch_size=1, with_key=True, with_headers=True, with_timestamp=True
        )
    )
    assert len(batches) == 2
    assert batches[0][1].column("_shape_key").to_pylist() == [b"a"]
    assert batches[1][1].column("_shape_key").to_pylist() == [b"b"]
    assert batches[0][1].column("_shape_headers").to_pylist() == [[("h", b"\xff")]]


def test_wanted1_null_and_empty_keys_and_fractional_time_boundary():
    h = KafkaHarness()
    b = with_event_fields(pa.RecordBatch.from_pydict({"customer": [None, ""]}), "orders", 0)
    h.make().emit(h.uri, [b], key="customer")
    assert [r[1] for r in h.store.log] == [b"", b""]
    from shape.streaming.emit.metadata import metadata

    t = batch().set_column(
        batch().schema.get_field_index("_shape_event_time"),
        "_shape_event_time",
        pa.array([datetime(2026, 1, 1, microsecond=999, tzinfo=UTC)] * 2),
    )
    assert [m[2] for m in metadata(t, timestamp="event_time")] == [1767225600000] * 2


@pytest.mark.parametrize("partition", [True, False, 1.5, -1, "1.5", "bad"])
def test_wanted1_kafka_partition_negative_boundaries(partition):
    h = KafkaHarness()
    with pytest.raises(ShapeError, match="partition"):
        h.make().emit(h.uri, [batch()], partition=partition)
    assert not h.delivered()


@pytest.mark.parametrize("harness", [KafkaHarness])
def test_wanted2_synthetic_and_dead_letter_names_retained(harness):
    h = harness()
    b = batch().append_column("_shape_dead_letter_reason", pa.array(["bad", "bad"]))
    h.make().emit(h.uri, [b], synthetic=True, headers=["custom=yes"])
    if True:
        headers = dict(h.store.log[0][3])
        assert headers["shape-synthetic"] == b"true"
        assert headers["shape-dead-letter-reason"] == b"bad"
    else:
        props = h.hub.batches[0].events[0].properties
        assert props["shape_synthetic"] is True
        assert props["shape-dead-letter-reason"] == b"bad"


@pytest.mark.parametrize("transport", ["kafka"])
def test_credentials_in_uri_are_refused_without_echo(transport):
    if transport == "kafka":
        from shape_kafka.emitter import parse_uri
    else:
        from shape_eventhubs.emitter import parse_uri
    with pytest.raises(ShapeError) as error:
        parse_uri(f"{transport}://user:credential-sentinel@host/events")
    assert "credential-sentinel" not in str(error.value)
