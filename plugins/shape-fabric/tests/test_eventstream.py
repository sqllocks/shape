"""The eventstream:// emitter against the in-memory hub (contract tests, every PR). The live
Eventstream is in ``test_live.py`` (needs secrets)."""

import pytest
from shape_fabric import EventstreamEmitter
from shape_fabric.eventstream import parse_uri
from shape_fabric.testing import EventstreamHarness

from shape.errors import ShapeError
from shape.plugins import kit
from shape.plugins.host import PluginHost
from shape.streaming.emit import contract

pytestmark = pytest.mark.contract


def test_the_emitter_contract(tmp_path):
    contract.check_contract(EventstreamHarness, directory=tmp_path)


def test_kit_conformance():
    h = EventstreamHarness()
    batch = next(iter(contract.default_plan().blocks(0))).batch.slice(0, 40)
    kit.check_emitter(h.make(), h.uri, [batch])


def test_the_plugin_registers_as_an_emitter():
    host = PluginHost(entry_points=lambda: [])
    host.register("shape.emitters", "eventstream", EventstreamEmitter, api="1.0", source="test")
    assert host.get("shape.emitters", "eventstream").schemes == ("eventstream",)


def test_messages_carry_the_idempotency_key():
    h = EventstreamHarness()
    batch = next(iter(contract.default_plan().blocks(0))).batch.slice(0, 4)
    h.make().emit(h.uri, [batch])
    assert [k for k, _ in h.delivered()] == [f"order_line/{i}" for i in range(4)]


def test_uris():
    assert parse_uri("eventstream://my-es") == ("my-es", None)
    assert parse_uri("eventstream://my-es/es_abc") == ("my-es", "es_abc")
    for bad in ("eventstream://", "eventstream://a/b/c", "eventhubs://a/b"):
        with pytest.raises(ShapeError):
            parse_uri(bad)


def test_the_connection_string_is_required(monkeypatch):
    monkeypatch.delenv("SHAPE_EVENTSTREAM_CONNECTION_STRING", raising=False)
    with pytest.raises(ShapeError, match="SHAPE_EVENTSTREAM_CONNECTION_STRING"):
        EventstreamEmitter().emit("eventstream://my-es", [])
