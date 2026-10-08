"""kafka:// and dead letters (W2-09 item 2): per-message rejections, encode failures, headers and
the command line end to end over the fake cluster."""

import json
from pathlib import Path

import pyarrow as pa
import pytest
from shape_kafka import KafkaEmitter
from shape_kafka.testing import EmitterHarness, FakeError, FakeRegistry

from shape.cli.main import main
from shape.plugins.host import PluginHost
from shape.plugins.registry import register_builtins
from shape.streaming.emit import (
    DeadLetterSink,
    EmitConfig,
    EmitRunner,
    RejectedEvents,
    contract,
    read_dead_letters,
)
from shape.streaming.emit.formats import encode_events, with_event_fields
from shape.streaming.emit.sinks import EmitterSink

pytestmark = pytest.mark.contract
REGISTRY = "http://registry.test:8081"


def batch_of(n: int = 10) -> pa.RecordBatch:
    return next(iter(contract.default_plan().blocks(0))).batch.slice(0, n)


def test_a_message_the_broker_refuses_for_good_is_rejected_and_the_others_are_delivered():
    h = EmitterHarness()
    h.inject_rejections(2)
    batch = batch_of(10)
    with pytest.raises(RejectedEvents) as err:
        h.make().emit(h.uri, [batch])
    assert err.value.keys == ["order_line/0", "order_line/1"]
    assert err.value.reasons == ["MSG_SIZE_TOO_LARGE"] * 2
    # the body is what was sent
    assert err.value.rejections[0].body == encode_events(batch)[0].body
    assert [k for _, k, _, _ in h.store.log] == [f"order_line/{i}".encode() for i in range(2, 10)]


def test_the_client_refusing_a_message_outright_is_a_rejection_too():
    h = EmitterHarness()
    h.store.refuse_at_produce = 1
    with pytest.raises(RejectedEvents) as err:
        h.make().emit(h.uri, [batch_of(5)])
    assert err.value.keys == ["order_line/0"] and err.value.reasons == ["_MSG_SIZE_TOO_LARGE"]
    assert len(h.store.log) == 4


def test_errors_that_are_not_about_one_message_are_not_rejections():
    class Down:
        """A producer whose every delivery fails with the given error."""

        def __init__(self, err):
            self.err = err

        def produce(self, topic, value=None, key=None, headers=None, on_delivery=None, **_):
            on_delivery(self.err, None)

        def poll(self, t=0):
            return 0

        def flush(self, t=-1):
            return 0

    for err in (
        FakeError("MSG_SIZE_TOO_LARGE", retriable=True),  # retryable: the runtime retries
        FakeError("TOPIC_AUTHORIZATION_FAILED"),  # the destination's, not the message's
        FakeError("_ALL_BROKERS_DOWN"),
    ):
        with pytest.raises(ConnectionError, match="not delivered"):
            KafkaEmitter(lambda cfg, err=err: Down(err)).emit("kafka://b:9092/t", [batch_of(3)])


def test_a_produce_time_error_that_is_not_about_the_message_is_raised():
    class Boom(Exception):
        pass

    class Broken:
        def produce(self, *a, **k):
            raise Boom(FakeError("_UNKNOWN_TOPIC"))

        def poll(self, t=0):
            return 0

        def flush(self, t=-1):
            return 0

    with pytest.raises(Boom):
        KafkaEmitter(lambda cfg: Broken()).emit("kafka://b:9092/t", [batch_of(3)])


def test_events_that_cannot_be_encoded_are_rejected_and_the_rest_delivered():
    big = pa.array([1, (1 << 63) + 1, 3, (1 << 63) + 2], pa.uint64())
    batch = with_event_fields(pa.RecordBatch.from_arrays([big], names=["n"]), "t", 0)
    h = EmitterHarness(registry=FakeRegistry())
    with pytest.raises(RejectedEvents) as err:
        h.make().emit(h.uri, [batch], event_format="avro", schema_registry_url=REGISTRY)
    assert err.value.keys == ["t/1", "t/3"]
    assert [k for _, k, _, _ in h.store.log] == [b"t/0", b"t/2"]
    assert all(r.body is None for r in err.value.rejections)  # the dead letter holds the flat JSON


def test_dead_letter_records_carry_the_reason_header_and_the_original_key():
    from shape.streaming.emit.formats import FIELD_DEAD_REASON

    batch = batch_of(3).append_column(FIELD_DEAD_REASON, pa.array(["r0", "r1", "r2"]))
    h = EmitterHarness()
    h.make().emit(h.uri, [batch])
    assert [dict(hd)["shape-dead-letter-reason"] for *_, hd in h.store.log] == [
        b"r0",
        b"r1",
        b"r2",
    ]
    assert all(b"_shape_dead_letter_reason" not in v for _, _, v, _ in h.store.log)
    plain = EmitterHarness()
    plain.make().emit(plain.uri, [batch_of(3)])
    assert all("shape-dead-letter-reason" not in dict(hd) for *_, hd in plain.store.log)


def test_the_run_dead_letters_to_a_second_topic_and_every_event_lands_once():
    h = EmitterHarness("orders")
    h.inject_rejections(6)
    main_sink = EmitterSink(h.make(), h.uri)
    dlq_uri = "kafka://broker-a:9092,broker-b:9092/orders.dlq"
    sink = DeadLetterSink(main_sink, EmitterSink(h.make(), dlq_uri), destination=h.uri)
    cfg = EmitConfig(max_events=1000, batch_events=250, retry_backoff=0.0)
    report = EmitRunner(contract.default_plan(), sink, cfg, dead_letter=sink).run()
    assert report.complete and report.dead_lettered == {"MSG_SIZE_TOO_LARGE": 6}
    main_keys = [k.decode() for t, k, _, _ in h.store.log if t == "orders"]
    dlq = [(k.decode(), v, hd) for t, k, v, hd in h.store.log if t == "orders.dlq"]
    assert len(main_keys) == 994 and len(dlq) == 6
    assert sorted(k for k, _, _ in dlq) == sorted(h.store.rejected)  # the original keys
    assert set(main_keys).isdisjoint(h.store.rejected)
    key, value, headers = dlq[0]
    rec = json.loads(value)
    assert rec["format"] == "shape-dead-letter" and rec["version"] == 1 and rec["key"] == key
    assert rec["reason"] == "MSG_SIZE_TOO_LARGE" and rec["destination"] == h.uri
    assert rec["attempts"] == 1 and rec["at"].endswith("Z")
    assert json.loads(rec["body"])["_shape_seq"] == rec["seq"]
    hd = dict(headers)
    assert (
        hd["shape-dead-letter-reason"] == b"MSG_SIZE_TOO_LARGE"
        and hd["shape-table"] == b"order_line"
    )


# ---- the command line, end to end ---------------------------------------------------------


def host_with(monkeypatch, h):
    host = PluginHost(entry_points=lambda: [])
    register_builtins(host)
    host.register("shape.emitters", "kafka", h.make, api="1.0", source="test")
    monkeypatch.setattr("shape.plugins.host.default_host", lambda: host)
    monkeypatch.setenv("SHAPE_CONFIRM_REMOTE", "1")  # W1-17: the broker is not on this machine


def test_shape_emit_dead_letters_encode_failures_to_a_file(monkeypatch, capsys, tmp_path: Path):
    """avro through the registry, with 5 events the destination refuses, to a JSON-lines file."""
    h = EmitterHarness("orders", FakeRegistry())
    h.inject_rejections(5)
    host_with(monkeypatch, h)
    dlq = tmp_path / "dlq.jsonl"
    ck = tmp_path / "ck.json"
    argv = [
        "emit", "retail", "--table", "order_line", "--max-events", "800", "--sink", h.uri,
        "--event-format", "avro", "--sink-config", f"kafka.schema_registry_url={REGISTRY}",
        "--dead-letter", f"file://{dlq}", "--checkpoint", str(ck), "--json",
    ]  # fmt: skip
    assert main(argv) == 0
    report = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert report["events"] == 800 and report["dead_lettered"] == {"MSG_SIZE_TOO_LARGE": 5}
    recs = list(read_dead_letters(str(dlq)))
    assert [r["key"] for r in recs] == [k for k in h.store.rejected]
    assert {r["destination"] for r in recs} == {h.uri}
    assert len(h.store.log) == 795
    assert json.loads(ck.read_text())["offset"] == 800


def test_shape_emit_max_dead_letter_exits_1(monkeypatch, capsys, tmp_path: Path):
    h = EmitterHarness("orders")
    h.inject_rejections(1000)
    host_with(monkeypatch, h)
    argv = [
        "emit", "retail", "--table", "order_line", "--max-events", "5000", "--sink", h.uri,
        "--dead-letter", f"file://{tmp_path}/dlq.jsonl", "--max-dead-letter", "3",
        "--batch-events", "50", "--json",
    ]  # fmt: skip
    assert main(argv) == 1
    cap = capsys.readouterr()
    report = json.loads(cap.out.strip().splitlines()[-1])
    assert report["stopped_by"] == "dead-letter-limit" and report["complete"] is False
    assert sum(report["dead_lettered"].values()) == 50  # the batch in hand was finished
    assert "more than 3 events were dead-lettered" in cap.err


def test_without_dead_letter_a_rejection_stops_the_run_with_exit_2(monkeypatch, capsys):
    h = EmitterHarness("orders")
    h.inject_rejections(1)
    host_with(monkeypatch, h)
    code = main(["emit", "retail", "--table", "order_line", "--max-events", "100", "--sink", h.uri])
    assert code == 2
    err = capsys.readouterr().err
    assert (
        "rejected 1 event (first order_line/0: MSG_SIZE_TOO_LARGE)" in err
        and "--dead-letter" in err
    )
