"""Dead-letter routing (W2-09 item 2): ``RejectedEvents``, ``DeadLetterSink``, the record format,
the checkpoint rule, ``--max-dead-letter`` and the command line."""

from __future__ import annotations

import base64
import datetime as dt
import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

from shape.cli.main import main
from shape.errors import ShapeError
from shape.streaming.emit import (
    DeadLetterSink,
    EmitConfig,
    EmitRunner,
    MemorySink,
    RejectedEvents,
    Rejection,
    contract,
    read_dead_letters,
)
from shape.streaming.emit.formats import (
    FIELD_DEAD_REASON,
    FIELD_SEQ,
    FIELD_TABLE,
    decode_line,
    encode_batch,
)


def keys_of(batch: pa.RecordBatch) -> list[str]:
    return [
        f"{t}/{s}"
        for t, s in zip(
            batch.column(FIELD_TABLE).to_pylist(), batch.column(FIELD_SEQ).to_pylist(), strict=True
        )
    ]


class RejectingSink(MemorySink):
    """Takes every event except those whose ``_shape_seq`` is in ``reject``; those it refuses
    with a non-retryable per-message error after taking the rest of the batch."""

    def __init__(
        self,
        reject: set[int] | None = None,
        *,
        reason: str = "MSG_SIZE_TOO_LARGE",
        body: bytes | None = None,
        fail_first: int = 0,
    ) -> None:
        super().__init__()
        self.reject = reject or set()
        self.reason = reason
        self.body = body
        self.fail_first = fail_first
        self.sends = 0

    def send(self, batch: pa.RecordBatch) -> None:
        self.sends += 1
        if self.fail_first > 0:
            self.fail_first -= 1
            raise ConnectionError("broker down")
        seqs = batch.column(FIELD_SEQ).to_pylist()
        bad = [i for i, s in enumerate(seqs) if s in self.reject]
        good = [i for i in range(batch.num_rows) if i not in set(bad)]
        if good:
            super().send(batch.take(pa.array(good)))
        if bad:
            keys = keys_of(batch.take(pa.array(bad)))
            raise RejectedEvents([Rejection(k, self.reason, self.body) for k in keys])


def plan():
    return contract.default_plan()


def first_batch(n: int = 50) -> pa.RecordBatch:
    return next(iter(plan().blocks(0))).batch.slice(0, n)


class Clock:
    def __call__(self) -> dt.datetime:
        return dt.datetime(2026, 10, 3, 12, 0, 1, 250000, tzinfo=dt.UTC)


def records(sink: MemorySink) -> list[dict[str, Any]]:
    out = []
    for b in sink.batches:
        out.extend(json.loads(x) for x in encode_batch(b).splitlines())
    return out


# ---- RejectedEvents -------------------------------------------------------------------------


def test_rejected_events_carries_keys_and_reasons_and_is_not_retried():
    exc = RejectedEvents([("t/1", "too big"), Rejection("t/2", "bad", b"x")])
    assert [r.key for r in exc.rejections] == ["t/1", "t/2"]
    assert exc.keys == ["t/1", "t/2"] and exc.reasons == ["too big", "bad"]
    assert isinstance(exc, ShapeError) and not isinstance(exc, OSError)
    assert "2 events" in str(exc) and "t/1" in str(exc) and "too big" in str(exc)
    with pytest.raises(ValueError, match="at least one"):
        RejectedEvents([])


def test_without_a_dead_letter_destination_a_rejection_stops_the_run():
    sink = RejectingSink({3})
    with pytest.raises(RejectedEvents) as err:
        EmitRunner(plan(), sink, EmitConfig(max_events=500, batch_events=100, retries=3)).run()
    assert err.value.keys == ["order_line/3"]
    assert "--dead-letter" in str(err.value)
    assert sink.sends == 1  # a rejection is not retried


# ---- the record format ----------------------------------------------------------------------


def test_a_rejected_event_goes_to_the_dead_letter_destination_as_a_record():
    batch = first_batch(10)
    primary, dlq = RejectingSink({4}, reason="MSG_SIZE_TOO_LARGE"), MemorySink()
    sink = DeadLetterSink(primary, dlq, destination="kafka://u:pw@broker:9092/orders", now=Clock())
    sink.send(batch)
    assert primary.num_events == 9  # every other event was delivered
    (rec,) = records(dlq)
    event = decode_line(encode_batch(batch.slice(4, 1)))
    assert rec == {
        "format": "shape-dead-letter",
        "version": 1,
        "key": "order_line/4",
        "table": "order_line",
        "seq": 4,
        "reason": "MSG_SIZE_TOO_LARGE",
        "destination": "kafka://u:***@broker:9092/orders",  # redacted
        "attempts": 1,
        "at": "2026-10-03T12:00:01.250000Z",
        "body": json.dumps(event, separators=(",", ":"), ensure_ascii=False),
        # the transport key of a dead-letter message is the original event's key
        "_shape_table": "order_line",
        "_shape_seq": 4,
    }
    assert sink.counts == {"MSG_SIZE_TOO_LARGE": 1} and sink.total == 1


def test_the_body_is_what_the_emitter_sent_when_it_says_so_and_base64_when_not_text():
    batch = first_batch(5)
    dlq = MemorySink()
    DeadLetterSink(RejectingSink({1}, body=b"\x00\xff\x10binary"), dlq, destination="x://d").send(
        batch
    )
    (rec,) = records(dlq)
    assert rec["body_encoding"] == "base64"
    assert base64.b64decode(rec["body"]) == b"\x00\xff\x10binary"
    dlq = MemorySink()
    DeadLetterSink(RejectingSink({1}, body="résumé".encode()), dlq, destination="x://d").send(batch)
    (rec,) = records(dlq)
    assert rec["body"] == "résumé" and "body_encoding" not in rec


def test_mixed_text_and_binary_bodies_in_one_batch():
    class Mixed(RejectingSink):
        def send(self, batch):
            raise RejectedEvents(
                [Rejection("order_line/0", "a", b"text"), Rejection("order_line/1", "b", b"\xff")]
            )

    dlq = MemorySink()
    DeadLetterSink(Mixed(), dlq, destination="x://d").send(first_batch(3))
    by_key = {r["key"]: r for r in records(dlq)}
    assert (
        by_key["order_line/0"]["body"] == "text" and "body_encoding" not in by_key["order_line/0"]
    )
    assert by_key["order_line/1"]["body_encoding"] == "base64"


def test_dead_letter_records_carry_the_reason_marker_column_for_transports_with_headers():
    dlq = MemorySink()
    DeadLetterSink(RejectingSink({2}, reason="too big"), dlq, destination="x://d").send(
        first_batch(5)
    )
    (b,) = dlq.batches
    assert b.column(FIELD_DEAD_REASON).to_pylist() == ["too big"]
    # the marker is never part of a body
    assert FIELD_DEAD_REASON not in records(dlq)[0]


def test_a_rejection_for_a_key_that_is_not_in_the_batch_is_an_error():
    class Liar(RejectingSink):
        def send(self, batch):
            raise RejectedEvents([("nowhere/9", "x")])

    with pytest.raises(ShapeError, match="nowhere/9"):
        DeadLetterSink(Liar(), MemorySink(), destination="x://d").send(first_batch(3))


def test_attempts_counts_the_deliveries_of_the_batch_up_to_the_rejection():
    primary = RejectingSink({1}, fail_first=2)
    dlq = MemorySink()
    sink = DeadLetterSink(primary, dlq, destination="x://d")
    batch = first_batch(4)
    for _ in range(2):
        with pytest.raises(ConnectionError):
            sink.send(batch)
    sink.send(batch)
    (rec,) = records(dlq)
    assert rec["attempts"] == 3


def test_the_record_format_is_read_back_and_a_newer_version_is_refused(tmp_path: Path):
    path = tmp_path / "dlq.jsonl"
    rec = {"format": "shape-dead-letter", "version": 1, "key": "t/1", "table": "t", "seq": 1}
    path.write_text(json.dumps(rec) + "\n" + json.dumps({**rec, "key": "t/2"}) + "\n")
    assert [r["key"] for r in read_dead_letters(str(path))] == ["t/1", "t/2"]
    for bad, msg in [
        ({**rec, "format": "other"}, "not a shape-dead-letter record"),
        ({**rec, "version": 2}, "version 2, which is newer than the version 1"),
        ({**rec, "version": "1"}, "integer"),
        ({**rec, "version": True}, "integer"),
    ]:
        path.write_text(json.dumps(bad) + "\n")
        with pytest.raises(ShapeError, match=msg):
            list(read_dead_letters(str(path)))
    path.write_text(json.dumps({"format": "shape-dead-letter"}) + "\n")
    with pytest.raises(ShapeError, match="integer"):
        list(read_dead_letters(str(path)))


# ---- through the runner: the checkpoint rule and the limits ---------------------------------


def run_with(primary, dlq, **cfg):
    sink = DeadLetterSink(primary, dlq, destination="x://d", max_dead_letter=cfg.pop("limit", None))
    cfg.setdefault("max_events", 600)
    cfg.setdefault("batch_events", 100)
    cfg.setdefault("retry_backoff", 0.0)
    runner = EmitRunner(plan(), sink, EmitConfig(**cfg), dead_letter=sink)
    return runner.run(), sink


def test_every_event_is_delivered_or_dead_lettered_exactly_once():
    primary, dlq = RejectingSink({5, 6, 250, 599}), MemorySink()
    report, _ = run_with(primary, dlq)
    assert report.complete and report.events == 600
    got = [k for b in primary.batches for k in keys_of(b)]
    lettered = [r["key"] for r in records(dlq)]
    assert lettered == ["order_line/5", "order_line/6", "order_line/250", "order_line/599"]
    assert sorted(got + lettered, key=lambda k: int(k.split("/")[1])) == [
        f"order_line/{i}" for i in range(600)
    ]
    assert report.dead_lettered == {"MSG_SIZE_TOO_LARGE": 4}


def test_the_report_counts_dead_letters_by_reason():
    class TwoReasons(RejectingSink):
        def send(self, batch):
            seqs = batch.column(FIELD_SEQ).to_pylist()
            bad = [(i, "odd" if s % 2 else "even") for i, s in enumerate(seqs) if s % 10 == 0]
            if bad:
                good = [i for i in range(batch.num_rows) if i not in {b[0] for b in bad}]
                super(RejectingSink, self).send(batch.take(pa.array(good)))
                keys = keys_of(batch)
                raise RejectedEvents([(keys[i], f"reason {r}") for i, r in bad])
            super(RejectingSink, self).send(batch)

    report, _ = run_with(TwoReasons(), MemorySink(), max_events=100)
    assert report.dead_lettered == {"reason even": 10}
    assert report.as_dict()["dead_lettered"] == {"reason even": 10}
    # no --dead-letter: an empty mapping, and the report stays what it was
    clean, _ = run_with(RejectingSink(), MemorySink(), max_events=10)
    assert clean.dead_lettered == {}


def test_the_checkpoint_does_not_move_past_a_dead_letter_the_destination_has_not_acknowledged(
    tmp_path: Path,
):
    class Failing(MemorySink):
        def send(self, batch):
            raise OSError("dead-letter destination down")

    ck = tmp_path / "ck.json"
    with pytest.raises(ShapeError, match="delivery failed after"):
        run_with(
            RejectingSink({250}),
            Failing(),
            checkpoint_path=str(ck),
            checkpoint_every=100,
            retries=1,
        )
    assert json.loads(ck.read_text())["offset"] <= 200  # the batch holding event 250 is 200..299


def test_a_flaky_dead_letter_destination_is_retried_without_sending_the_batch_twice():
    class Flaky(MemorySink):
        fails = 2

        def send(self, batch):
            if self.fails:
                self.fails -= 1
                raise ConnectionError("blip")
            super().send(batch)

    primary, dlq = RejectingSink({250}), Flaky()
    report, _ = run_with(primary, dlq, retries=3)
    assert report.complete and report.retries == 2
    assert [r["key"] for r in records(dlq)] == ["order_line/250"]
    assert primary.sends == 6  # six batches of 100, none repeated


def test_a_resumed_run_dead_letters_again_and_the_key_removes_the_repeats(tmp_path: Path):
    ck = tmp_path / "ck.json"
    primary, dlq = RejectingSink({150, 450}), MemorySink()
    run_with(primary, dlq, checkpoint_path=str(ck), checkpoint_every=100, max_events=300)
    doc = json.loads(ck.read_text())
    doc.update(offset=100, complete=False)  # a crash: the checkpoint is behind
    ck.write_text(json.dumps(doc))
    run_with(primary, dlq, checkpoint_path=str(ck), checkpoint_every=100)
    keys = [r["key"] for r in records(dlq)]
    assert sorted(set(keys)) == ["order_line/150", "order_line/450"]
    assert len(keys) == 3  # 150 twice (before and after the crash), 450 once


@pytest.mark.parametrize(
    ("limit", "stops"),
    [(0, True), (1, True), (3, True), (4, False), (5, False), (None, False)],
)
def test_max_dead_letter_stops_once_more_than_n_were_dead_lettered(limit, stops):
    # four events, in four different batches of 100
    report, sink = run_with(RejectingSink({10, 110, 210, 310}), MemorySink(), limit=limit)
    if stops:
        assert not report.complete and report.stopped_by == "dead-letter-limit"
        assert sink.limit_exceeded and sink.total == limit + 1
        assert report.end_offset == 100 * (limit + 1)  # the batch that crossed the limit counts
    else:
        assert report.complete and not sink.limit_exceeded and sink.total == 4


def test_a_limit_crossed_inside_one_batch_finishes_the_batch():
    report, sink = run_with(RejectingSink({1, 2, 3, 4, 5}), MemorySink(), limit=2)
    assert report.stopped_by == "dead-letter-limit" and sink.total == 5
    assert report.end_offset == 100


def test_the_limit_must_be_zero_or_more():
    with pytest.raises(ShapeError, match="0 or more"):
        DeadLetterSink(MemorySink(), MemorySink(), destination="x://d", max_dead_letter=-1)


def test_a_fan_out_merges_the_rejections_of_its_sinks_and_delivers_to_the_rest():
    from shape.streaming.emit import FanOutSink

    a, b, c = RejectingSink({1}, reason="a"), RejectingSink({2}, reason="b"), MemorySink()
    dlq = MemorySink()
    DeadLetterSink(FanOutSink([a, b, c]), dlq, destination="x://d").send(first_batch(5))
    assert [r["key"] for r in records(dlq)] == ["order_line/1", "order_line/2"]
    assert [r["reason"] for r in records(dlq)] == ["a", "b"]
    assert c.num_events == 5 and a.num_events == 4 and b.num_events == 4


def test_a_fan_out_retries_only_the_sink_that_failed_and_keeps_the_rejections():
    from shape.streaming.emit import FanOutSink

    a, b = RejectingSink({1}, reason="a"), RejectingSink(fail_first=1)
    dlq = MemorySink()
    sink = DeadLetterSink(FanOutSink([a, b]), dlq, destination="x://d")
    batch = first_batch(5)
    with pytest.raises(ConnectionError):
        sink.send(batch)
    sink.send(batch)
    assert a.sends == 1 and b.sends == 2 and b.num_events == 5
    assert [r["key"] for r in records(dlq)] == ["order_line/1"]


# ---- the command line -----------------------------------------------------------------------

BASE = ["emit", "retail", "--table", "order_line", "--max-events", "300"]


def test_dead_letter_needs_a_different_destination_and_max_needs_dead_letter(
    capsys, tmp_path: Path
):
    out = tmp_path / "e.jsonl"
    assert main([*BASE, "--sink", f"file://{out}", "--dead-letter", f"file://{out}"]) == 2
    assert "must differ" in capsys.readouterr().err
    assert main([*BASE, "--max-dead-letter", "3"]) == 2
    assert "--max-dead-letter needs --dead-letter" in capsys.readouterr().err
    assert (
        main([*BASE, "--dead-letter", f"file://{tmp_path}/d.jsonl", "--max-dead-letter", "-1"]) == 2
    )
    assert "0 or more" in capsys.readouterr().err
    assert main([*BASE, "--dead-letter", "nope://x"]) == 2
    assert "unknown sink" in capsys.readouterr().err


def test_a_run_without_rejections_with_a_dead_letter_file_is_unchanged(capsys, tmp_path: Path):
    dlq = tmp_path / "dlq.jsonl"
    assert main([*BASE]) == 0
    plain = capsys.readouterr().out
    assert main([*BASE, "--dead-letter", f"file://{dlq}"]) == 0
    assert capsys.readouterr().out == plain
    assert not dlq.exists() or dlq.read_text() == ""  # nothing was dead-lettered
