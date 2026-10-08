"""The kafka:// emitter against the in-memory producer (contract tests, every PR).

The real broker runs in ``test_emitter_emulator.py`` (nightly).
"""

import json
import sys

import pytest
from shape_kafka import KafkaEmitter
from shape_kafka.emitter import parse_uri
from shape_kafka.testing import EmitterHarness

from shape.errors import ShapeError
from shape.plugins import kit
from shape.plugins.host import PluginHost
from shape.plugins.registry import register_builtins
from shape.streaming.emit import contract
from shape.streaming.emit.formats import encode_events

pytestmark = pytest.mark.contract


def test_the_emitter_contract(tmp_path):
    contract.check_contract(EmitterHarness, directory=tmp_path)


def test_kit_conformance():
    h = EmitterHarness()
    batch = next(iter(contract.default_plan().blocks(0))).batch.slice(0, 50)
    kit.check_emitter(h.make(), h.uri, [batch])


def test_the_plugin_is_discovered_as_an_emitter():
    host = PluginHost(entry_points=lambda: [])
    host.register("shape.emitters", "kafka", KafkaEmitter, api="1.0", source="test")
    assert host.get("shape.emitters", "kafka").schemes == ("kafka",)


def test_every_message_is_keyed_by_the_idempotency_key_and_names_its_table():
    h = EmitterHarness()
    batch = next(iter(contract.default_plan().blocks(0))).batch.slice(0, 5)
    h.make().emit(h.uri, [batch])
    assert [k for _, k, _, _ in h.store.log] == [f"order_line/{i}".encode() for i in range(5)]
    assert all(t == "events" for t, *_ in h.store.log)
    assert all(hd == [("shape-table", b"order_line")] for *_, hd in h.store.log)
    assert [v for _, _, v, _ in h.store.log] == [e.body for e in encode_events(batch)]


def test_defaults_are_acks_all_and_idempotent_and_the_uri_gives_the_servers():
    h = EmitterHarness()
    h.make().emit(h.uri, [], config={"linger.ms": 50})
    # a producer is built on first use only; an empty emit still creates it
    cfg = h.store.producers[0].config
    assert cfg["acks"] == "all" and cfg["enable.idempotence"] is True and cfg["linger.ms"] == 50
    assert cfg["bootstrap.servers"] == "broker-a:9092,broker-b:9092"


def test_one_producer_is_reused_across_batches():
    h = EmitterHarness()
    e = h.make()
    batch = next(iter(contract.default_plan().blocks(0))).batch.slice(0, 5)
    for _ in range(3):
        e.emit(h.uri, [batch])
    assert len(h.store.producers) == 1 and len(h.store.log) == 15


def test_an_unacknowledged_message_is_a_timeout_not_a_success():
    class Stuck:
        def produce(self, *a, **k):
            pass

        def poll(self, t=0):
            return 0

        def flush(self, t=-1):
            return 3

    batch = next(iter(contract.default_plan().blocks(0))).batch.slice(0, 3)
    with pytest.raises(TimeoutError, match="not acknowledged"):
        KafkaEmitter(lambda cfg: Stuck()).emit("kafka://b:9092/t", [batch], flush_timeout=1)


@pytest.mark.parametrize(
    "uri", ["kafka:///t", "kafka://b:9092", "kafka://b:9092/a/b", "http://b:9092/t"]
)
def test_bad_uris_are_shape_errors(uri):
    with pytest.raises(ShapeError):
        parse_uri(uri)


def test_unknown_options_and_envelopes_are_refused():
    h = EmitterHarness()
    with pytest.raises(ShapeError, match="unknown kafka emitter options"):
        h.make().emit(h.uri, [], bogus=1)
    with pytest.raises(ShapeError, match="unknown envelope"):
        h.make().emit(h.uri, [], envelope="xml")


def test_a_missing_client_library_is_a_clear_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "confluent_kafka", None)
    with pytest.raises(ShapeError, match="pip install sqllocks-shape-kafka"):
        KafkaEmitter().emit("kafka://b:9092/t", [])


def test_shape_emit_through_the_plugin(monkeypatch, capsys, tmp_path):
    """``shape emit --sink kafka://...`` end to end over the fake cluster, with a checkpoint."""
    from shape.cli.main import main

    monkeypatch.setenv("SHAPE_CONFIRM_REMOTE", "1")  # about the emitter, not the confirmation
    h = EmitterHarness()
    host = PluginHost(entry_points=lambda: [])
    register_builtins(host)
    host.register("shape.emitters", "kafka", h.make, api="1.0", source="test")
    monkeypatch.setattr("shape.plugins.host.default_host", lambda: host)
    ck = tmp_path / "ck.json"
    argv = ["emit", "retail", "--table", "order_line", "--max-events", "1200", "--sink", h.uri]
    assert main([*argv, "--checkpoint", str(ck), "--json"]) == 0
    report = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert report["events"] == 1200 and len(h.store.log) == 1200
    assert json.loads(ck.read_text())["offset"] == 1200
    assert main([*argv, "--checkpoint", str(ck), "--json"]) == 0  # a finished run is not repeated
    assert len(h.store.log) == 1200


def test_every_message_can_carry_the_synthetic_header_and_poison_is_accepted():
    batch = next(iter(contract.default_plan().blocks(0))).batch.slice(0, 5)
    h = EmitterHarness()
    h.make().emit(h.uri, [batch])
    assert all(hd == [("shape-table", b"order_line")] for *_, hd in h.store.log)
    h = EmitterHarness()
    emitter = h.make()
    emitter.emit(h.uri, [batch], synthetic=True)
    assert all(
        hd == [("shape-table", b"order_line"), ("shape-synthetic", b"true")]
        for *_, hd in h.store.log
    )
    assert emitter.supports_synthetic and emitter.accepts_poison
