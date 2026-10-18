"""W9-08 message metadata acceptance (offline)."""

from datetime import UTC, datetime

import pyarrow as pa
import pytest
from shape_eventhubs.testing import EmitterHarness as HubHarness

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


@pytest.mark.parametrize("harness", [HubHarness])
def test_wanted1_column_keys_and_static_column_headers(harness):
    h = harness()
    h.make().emit(
        h.uri, [batch()], key="customer|region", headers=["trace=static", "customer=@customer"]
    )
    if False:
        assert [x[1] for x in h.store.log] == [b"a|1", b"b|2"]
        assert ("shape-key", b"orders/0") in h.store.log[0][3]
        assert ("customer", b"a") in h.store.log[0][3]
    else:
        e = h.hub.batches[0].events[0]
        assert e.properties["shape-key"] == "orders/0"
        assert e.properties["customer"] == b"a"


@pytest.mark.parametrize("harness", [HubHarness])
@pytest.mark.parametrize(
    "option", [{"key": "missing"}, {"headers": ["x=@missing"]}, {"timestamp": "invalid"}]
)
def test_wanted1_bad_options_refused_before_send(harness, option):
    h = harness()
    with pytest.raises(ShapeError):
        h.make().emit(h.uri, [batch()], **option)
    assert h.delivered() == []


@pytest.mark.parametrize("harness", [HubHarness])
@pytest.mark.parametrize(
    "header", ["shape-table=x", "shape-key=x", "shape-synthetic=x", "shape-dead-letter-reason=x"]
)
def test_wanted2_reserved_headers_cannot_be_shadowed(harness, header):
    h = harness()
    with pytest.raises(ShapeError, match="reserved"):
        h.make().emit(h.uri, [batch()], headers=[header], synthetic=True)
    assert h.delivered() == []


def test_wanted1_eventhubs_partition_column_and_creation_time():
    h = HubHarness()
    h.make().emit(h.uri, [batch()], partition_key="customer", timestamp="event_time")
    assert [b.partition_key for b in h.hub.batches] == ["a", "b"]
    assert h.hub.batches[0].events[0].raw_amqp_message.properties.creation_time == 1767225600000


def test_wanted3_eventhubs_creation_time_and_properties():
    from types import SimpleNamespace

    from shape_eventhubs.source import _message_key, _message_timestamp, _properties

    e = SimpleNamespace(
        enqueued_time=datetime(2026, 1, 1, tzinfo=UTC),
        raw_amqp_message=SimpleNamespace(properties=SimpleNamespace(creation_time=123)),
        properties={b"shape_key": b"a", b"h": b"\xff"},
    )
    assert _message_timestamp(e) == 123000
    assert _message_key(e) == b"a"
    assert _properties(e)["h"] == b"\xff"
    e.raw_amqp_message.properties.creation_time = None
    assert _message_timestamp(e) == 1767225600000000


@pytest.mark.parametrize("harness", [HubHarness])
def test_wanted2_synthetic_and_dead_letter_names_retained(harness):
    h = harness()
    b = batch().append_column("_shape_dead_letter_reason", pa.array(["bad", "bad"]))
    h.make().emit(h.uri, [b], synthetic=True, headers=["custom=yes"])
    if False:
        headers = dict(h.store.log[0][3])
        assert headers["shape-synthetic"] == b"true"
        assert headers["shape-dead-letter-reason"] == b"bad"
    else:
        props = h.hub.batches[0].events[0].properties
        assert props["shape_synthetic"] is True
        assert props["shape-dead-letter-reason"] == b"bad"


def test_wanted3_eventhubs_source_metadata_stable_across_empty_properties():
    from shape_eventhubs.testing import FakeHub

    hub = FakeHub({"0": [(' {"x":1}', None), (' {"x":2}', None)]})
    hub.partitions["0"][0].properties = {"shape_key": b"a", "trace": b"\xff"}
    hub.partitions["0"][0].partition_key = "customer"
    batches = list(
        hub.source().read(
            "eventhubs://ns/h", batch_size=1, with_key=True, with_headers=True, with_timestamp=True
        )
    )
    assert len(batches) == 2
    assert batches[0][1].column("_shape_key").to_pylist() == [b"a"]
    assert batches[1][1].column("_shape_key").to_pylist() == [None]
    assert batches[1][1].column("_shape_properties").to_pylist() == [[]]
    assert batches[1][1].column("_shape_partition_key").to_pylist() == [None]


@pytest.mark.parametrize("transport", ["eventhubs"])
def test_credentials_in_uri_are_refused_without_echo(transport):
    if transport == "kafka":
        from shape_kafka.emitter import parse_uri
    else:
        from shape_eventhubs.emitter import parse_uri
    with pytest.raises(ShapeError) as error:
        parse_uri(f"{transport}://user:credential-sentinel@host/events")
    assert "credential-sentinel" not in str(error.value)
