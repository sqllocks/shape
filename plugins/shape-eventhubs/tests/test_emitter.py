"""The eventhubs:// emitter against the in-memory hub (contract tests, every PR).

The emulator runs in ``test_emitter_emulator.py`` (nightly).
"""

import sys

import pytest
from shape_eventhubs import EventHubsEmitter
from shape_eventhubs.emitter import parse_uri
from shape_eventhubs.testing import EmitterHarness

from shape.errors import ShapeError
from shape.plugins import kit
from shape.plugins.host import PluginHost
from shape.streaming.emit import contract

pytestmark = pytest.mark.contract


def test_the_emitter_contract(tmp_path):
    contract.check_contract(EmitterHarness, directory=tmp_path)


def test_the_emitter_contract_with_tiny_service_batches(tmp_path):
    # Every runtime batch is split into many service batches; the contract still holds.
    contract.check_contract(lambda: EmitterHarness(max_batch_bytes=3_000), directory=tmp_path)


def test_kit_conformance():
    h = EmitterHarness()
    batch = next(iter(contract.default_plan().blocks(0))).batch.slice(0, 50)
    kit.check_emitter(h.make(), h.uri, [batch])


def test_the_plugin_registers_as_an_emitter():
    host = PluginHost(entry_points=lambda: [])
    host.register("shape.emitters", "eventhubs", EventHubsEmitter, api="1.0", source="test")
    assert host.get("shape.emitters", "eventhubs").schemes == ("eventhubs",)


def test_messages_carry_the_key_properties_and_content_type():
    h = EmitterHarness()
    batch = next(iter(contract.default_plan().blocks(0))).batch.slice(0, 5)
    h.make().emit(h.uri, [batch])
    events = [e for b in h.hub.batches for e in b.events]
    assert [e.properties["shape_key"] for e in events] == [f"order_line/{i}" for i in range(5)]
    assert [e.properties["shape_seq"] for e in events] == list(range(5))
    assert all(e.properties["shape_table"] == "order_line" for e in events)
    assert all(e.content_type == "application/json" for e in events)
    assert all(b.partition_key == "order_line" for b in h.hub.batches)
    h2 = EmitterHarness()
    h2.make().emit(h2.uri, [batch], envelope="cloudevents", partition_key="none")
    assert all(
        e.content_type == "application/cloudevents+json" for b in h2.hub.batches for e in b.events
    )
    assert all(b.partition_key is None for b in h2.hub.batches)


def test_batches_are_split_by_table_and_by_size():
    import pyarrow as pa

    from shape.streaming.emit.formats import with_event_fields

    a = with_event_fields(pa.RecordBatch.from_pydict({"x": [1, 2, 3]}), "a", 0)
    b = with_event_fields(pa.RecordBatch.from_pydict({"x": [4, 5]}), "b", 0)
    mixed = pa.concat_batches([a, b])
    h = EmitterHarness()
    h.make().emit(h.uri, [mixed])
    assert [(x.partition_key, len(x)) for x in h.hub.batches] == [("a", 3), ("b", 2)]
    small = EmitterHarness(max_batch_bytes=220)
    small.make().emit(small.uri, [a])
    assert sum(len(x) for x in small.hub.batches) == 3 and len(small.hub.batches) > 1


def test_an_event_larger_than_a_batch_is_an_error_not_a_loop():
    h = EmitterHarness(max_batch_bytes=10)
    batch = next(iter(contract.default_plan().blocks(0))).batch.slice(0, 1)
    with pytest.raises(ShapeError, match="larger than an event hub batch"):
        h.make().emit(h.uri, [batch])


def test_a_service_that_stays_busy_is_a_retryable_connection_error():
    h = EmitterHarness()
    h.hub.busy = 100
    batch = next(iter(contract.default_plan().blocks(0))).batch.slice(0, 3)
    with pytest.raises(ConnectionError, match="stayed busy"):
        h.make().emit(h.uri, [batch], busy_retries=2)
    assert h.hub.hits == 3 and not h.hub.batches


def test_close_closes_the_clients():
    h = EmitterHarness()
    e = h.make()
    e.emit(h.uri, [])
    e.close()
    e.close()
    assert [c.closed for c in h.hub.clients] == [True]


@pytest.mark.parametrize(
    "uri", ["eventhubs:///h", "eventhubs://ns", "eventhubs://ns/a/b", "kafka://ns/h"]
)
def test_bad_uris_are_shape_errors(uri):
    with pytest.raises(ShapeError):
        parse_uri(uri)


def test_unknown_options_are_refused():
    h = EmitterHarness()
    with pytest.raises(ShapeError, match="unknown eventhubs emitter options"):
        h.make().emit(h.uri, [], bogus=1)
    with pytest.raises(ShapeError, match="partition_key"):
        h.make().emit(h.uri, [], partition_key="x")


def test_a_missing_client_library_is_a_clear_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "azure.eventhub", None)
    with pytest.raises(ShapeError, match="pip install sqllocks-shape-eventhubs"):
        EventHubsEmitter().emit("eventhubs://ns/h", [])


def test_without_a_connection_string_or_azure_identity_the_error_says_what_to_give(monkeypatch):
    monkeypatch.delenv("SHAPE_EVENTHUBS_CONNECTION_STRING", raising=False)
    monkeypatch.setitem(sys.modules, "azure.identity", None)
    with pytest.raises(ShapeError, match="SHAPE_EVENTHUBS_CONNECTION_STRING"):
        EventHubsEmitter().emit("eventhubs://ns/h", [])


def test_every_message_can_carry_the_synthetic_property_and_poison_is_accepted():
    batch = next(iter(contract.default_plan().blocks(0))).batch.slice(0, 5)
    h = EmitterHarness()
    h.make().emit(h.uri, [batch])
    assert all("shape_synthetic" not in e.properties for b in h.hub.batches for e in b.events)
    h = EmitterHarness()
    emitter = h.make()
    emitter.emit(h.uri, [batch], synthetic=True)
    assert all(e.properties["shape_synthetic"] is True for b in h.hub.batches for e in b.events)
    assert emitter.supports_synthetic and emitter.accepts_poison
