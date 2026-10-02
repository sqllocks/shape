"""EventstreamWriter: a table's rows as events over the Event Hubs protocol."""

import json

import pytest
from shape_eventhubs.testing import FakeProducerHub
from shape_fabric import EventstreamWriter, WriteError
from shape_fabric.testing import sample_batch

from shape.errors import ShapeError

URI = "eventstream://my-eventstream"


def events(hub):
    return [e for batch in hub.batches for e in batch.events]


def body(event):
    return b"".join(event.body)


def make(**kw):
    hub = FakeProducerHub()
    return EventstreamWriter(URI, client_factory=hub.factory, busy_pause=0.001, **kw), hub


def test_rows_become_flat_events_with_the_idempotency_key(batches):
    w, hub = make()
    assert w.write_table("customer", batches) == 7
    sent = events(hub)
    assert len(sent) == 7
    first = json.loads(body(sent[0]))
    assert first["_shape_table"] == "customer" and first["_shape_seq"] == 0 and first["id"] == 0
    assert [json.loads(body(e))["_shape_seq"] for e in sent] == list(range(7))  # across batches
    assert sent[0].properties["shape_key"] == "customer/0"


def test_cloudevents_envelope(batches):
    w, hub = make(envelope="cloudevents")
    w.write_table("customer", batches)
    assert json.loads(body(events(hub)[2]))["id"] == "customer/2"


def test_a_column_named_like_an_event_field_is_refused():
    import pyarrow as pa

    w, _ = make()
    batch = pa.RecordBatch.from_arrays([pa.array([1])], names=["_shape_seq"])
    with pytest.raises(ShapeError, match="reserved"):
        w.write_table("t", [batch])


def test_write_tables_and_a_failing_hub(batches):
    w, hub = make()
    result = w.write_tables({"a": batches, "b": [sample_batch(0, 2)]})
    assert result.per_table == {"a": 7, "b": 2}
    hub.failures = 10**6
    with pytest.raises(WriteError):
        w.write_tables({"c": batches})


def test_a_connection_string_is_required_without_a_client():
    w = EventstreamWriter(URI)
    with pytest.raises(ShapeError, match="needs its connection string"):
        w.write_table("t", [sample_batch(0, 1)])
