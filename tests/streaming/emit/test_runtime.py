"""The runner: limits, rate, bursts, backpressure, retries, checkpoint, resume (P5-01)."""

from __future__ import annotations

import gc
import json
import threading
import time
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

from shape.errors import ShapeError
from shape.streaming.checkpoint import CheckpointError, FileCheckpointStore
from shape.streaming.emit import (
    Burst,
    EmitConfig,
    EmitRunner,
    EventPlan,
    MemorySink,
)
from shape.streaming.emit.formats import FIELD_SEQ, FIELD_TABLE


def _plan(engine: Any, **kwargs: Any) -> EventPlan:
    kwargs.setdefault("tables", ["order_line"])
    return EventPlan(engine, **kwargs)


def _seqs(sink: MemorySink) -> list[int]:
    return [s for b in sink.batches for s in b.column(FIELD_SEQ).to_pylist()]


def test_defaults_are_not_realtime(retail_engine) -> None:
    assert EmitConfig().realtime is False
    sink = MemorySink()
    t0 = time.perf_counter()
    report = EmitRunner(_plan(retail_engine), sink).run()
    assert time.perf_counter() - t0 < 5
    assert report.complete and report.stopped_by == "complete"
    assert report.events == 12500 == sink.num_events
    assert _seqs(sink) == list(range(12500))
    assert report.max_lag == 0.0


@pytest.mark.parametrize("n", [0, 1, 999, 1000, 1001, 12499])
def test_max_events_is_exact(retail_engine, n: int) -> None:
    sink = MemorySink()
    report = EmitRunner(_plan(retail_engine), sink, EmitConfig(max_events=n)).run()
    assert sink.num_events == report.events == n
    assert report.complete and report.stopped_by == "max-events"


def test_max_events_above_the_data_ends_with_the_data(retail_engine) -> None:
    sink = MemorySink()
    report = EmitRunner(_plan(retail_engine), sink, EmitConfig(max_events=10**9)).run()
    assert report.events == 12500 and report.stopped_by == "complete"


def test_batch_size(retail_engine) -> None:
    sink = MemorySink()
    EmitRunner(_plan(retail_engine), sink, EmitConfig(batch_events=250)).run()
    assert {b.num_rows for b in sink.batches} == {250}
    assert len(sink.batches) == 50


def test_duration_stops_a_realtime_run(retail_engine, tmp_path: Path) -> None:
    sink = MemorySink()
    cfg = EmitConfig(realtime=True, rate=1000, duration=1.0, checkpoint_path=str(tmp_path / "c"))
    report = EmitRunner(_plan(retail_engine), sink, cfg).run()
    assert report.stopped_by == "duration" and not report.complete
    assert 900 <= report.events <= 1100
    doc = json.loads((tmp_path / "c").read_text())
    assert doc["offset"] == report.events == report.end_offset and doc["complete"] is False


def test_duration_also_bounds_a_fast_run(retail_engine) -> None:
    class Slow(MemorySink):
        def send(self, batch: pa.RecordBatch) -> None:
            time.sleep(0.05)
            super().send(batch)

    report = EmitRunner(
        _plan(retail_engine), Slow(), EmitConfig(duration=0.5, batch_events=100)
    ).run()
    assert report.stopped_by == "duration" and 5 <= report.events // 100 <= 12


def _assert_rate_holds(
    report: Any, rate: int = 2000, *, tolerance: float = 0.05, max_lag: float = 0.05
) -> None:
    assert report.complete
    assert abs(report.rate / rate - 1) < 0.05, report.rate
    # and in every full second
    for second, n in enumerate(report.per_second[:-1]):
        assert abs(n / rate - 1) < tolerance, (second, n, report.per_second)
    assert report.max_lag < max_lag, report.max_lag


def test_realtime_rate_within_five_percent(retail_engine) -> None:
    sink = MemorySink()
    cfg = EmitConfig(realtime=True, rate=2000, max_events=12000)
    report = EmitRunner(_plan(retail_engine), sink, cfg).run()
    _assert_rate_holds(report)


class _CollectNearSecondBoundary(MemorySink):
    """Starts a full garbage collection on another thread just before the first second ends: a
    long-lived host process (a test run, a notebook) meets one of these at an arbitrary moment."""

    def __init__(self) -> None:
        super().__init__()
        self.timer: threading.Timer | None = None

    def send(self, batch: pa.RecordBatch) -> None:
        if self.timer is None:
            self.timer = threading.Timer(0.93, gc.collect)
            self.timer.start()
        super().send(batch)


def test_realtime_rate_holds_through_a_full_collection_of_a_large_heap(retail_engine) -> None:
    """A full collection scans every tracked object the process holds, and holds the GIL while it
    does. The pacing must not pay for the heap the host built before the run (the P5-01b cause of
    the rate test failing in a full suite run and never in isolation)."""
    heap: list[list[None]] = []
    while True:
        heap.extend([None] for _ in range(250_000))
        started = time.perf_counter()
        gc.collect()
        full = time.perf_counter() - started
        if full >= 0.2 or len(heap) >= 3_000_000:
            break
    try:
        sink = _CollectNearSecondBoundary()
        cfg = EmitConfig(realtime=True, rate=2000, max_events=8000)
        report = EmitRunner(_plan(retail_engine), sink, cfg).run()
        assert sink.timer is not None
        sink.timer.join()
        # Without the freeze the pacing waits out the whole collection (`full`); the allowance is
        # half of it, which is more than the stalls a busy shared host adds on its own.
        _assert_rate_holds(report, tolerance=0.10, max_lag=max(0.1, full / 2))
        assert not gc.get_freeze_count()  # the freeze is lifted when the run ends
    finally:
        del heap
        gc.collect()


def test_realtime_rate_holds_while_the_checkpoint_write_is_slow(
    retail_engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ``fsync`` on a busy disk takes tens of milliseconds; it must not delay the pacing, and
    the checkpoint must still never run ahead of what was delivered."""
    real_save = FileCheckpointStore.save_document
    saved: list[int] = []

    def slow_save(self: FileCheckpointStore, document: dict[str, Any]) -> None:
        time.sleep(0.4)
        real_save(self, document)
        saved.append(int(document["offset"]))

    monkeypatch.setattr(FileCheckpointStore, "save_document", slow_save)
    ck = tmp_path / "c"
    sink = MemorySink()
    cfg = EmitConfig(
        realtime=True, rate=2000, max_events=8000, checkpoint_path=str(ck), checkpoint_seconds=0.5
    )
    report = EmitRunner(_plan(retail_engine), sink, cfg).run()
    # a synchronous write would hold the pacing for the 0.4 s of each save
    _assert_rate_holds(report, tolerance=0.10, max_lag=0.2)
    assert report.checkpoints >= 3 and saved == sorted(saved)
    doc = json.loads(ck.read_text())
    assert doc["offset"] == 8000 and doc["complete"] is True
    assert saved[-1] == 8000 and sink.num_events == 8000


def test_bursts(retail_engine) -> None:
    sink = MemorySink()
    cfg = EmitConfig(realtime=True, rate=1000, bursts=(Burst(1, 1, 3),), max_events=5000)
    report = EmitRunner(_plan(retail_engine), sink, cfg).run()
    ps = report.per_second
    assert abs(ps[0] / 1000 - 1) < 0.1 and abs(ps[1] / 3000 - 1) < 0.1, ps
    assert abs(ps[2] / 1000 - 1) < 0.1, ps


def test_realtime_resume_starts_its_own_clock(retail_engine, tmp_path: Path) -> None:
    cfg = EmitConfig(realtime=True, rate=2000, max_events=4000, checkpoint_path=str(tmp_path / "c"))
    EmitRunner(_plan(retail_engine), MemorySink(), cfg).run()
    cfg2 = EmitConfig(
        realtime=True, rate=2000, max_events=6000, checkpoint_path=str(tmp_path / "c")
    )
    sink = MemorySink()
    report = EmitRunner(_plan(retail_engine), sink, cfg2).run()
    assert report.start_offset == 4000 and report.events == 2000
    assert abs(report.rate / 2000 - 1) < 0.1


def test_backpressure_blocks_the_generator(retail_engine) -> None:
    class Slow(MemorySink):
        def send(self, batch: pa.RecordBatch) -> None:
            time.sleep(0.01)
            super().send(batch)

    report = EmitRunner(
        _plan(retail_engine),
        Slow(),
        EmitConfig(batch_events=100, queue_batches=3, max_events=2000),
    ).run()
    assert report.events == 2000
    assert report.max_queue_depth == 3  # the queue filled, so the generator had to wait


def test_slow_sink_in_realtime_falls_behind_without_dropping(retail_engine) -> None:
    class Slow(MemorySink):
        def send(self, batch: pa.RecordBatch) -> None:
            time.sleep(0.02)  # 100-event batches at 20 ms: 5,000/s at most
            super().send(batch)

    sink = Slow()
    cfg = EmitConfig(realtime=True, rate=10000, batch_events=100, max_events=1500)
    report = EmitRunner(_plan(retail_engine), sink, cfg).run()
    assert _seqs(sink) == list(range(1500))
    assert report.max_lag > 0.1
    assert report.rate < 6000


class Flaky(MemorySink):
    def __init__(self, failures: list[int], error: type[Exception] = ConnectionError) -> None:
        super().__init__()
        self.failures, self.error, self.calls = failures, error, 0

    def send(self, batch: pa.RecordBatch) -> None:
        self.calls += 1
        if self.failures and self.calls in self.failures:
            raise self.error("down")
        super().send(batch)


def test_transient_failures_are_retried(retail_engine) -> None:
    sink = Flaky([2, 3, 7])
    cfg = EmitConfig(batch_events=500, retry_backoff=0.001, max_events=5000)
    report = EmitRunner(_plan(retail_engine), sink, cfg).run()
    assert report.retries == 3 and report.complete
    assert _seqs(sink) == list(range(5000))


def test_a_persistent_failure_checkpoints_the_last_delivery(retail_engine, tmp_path: Path) -> None:
    sink = Flaky(list(range(4, 100)), OSError)
    ck = tmp_path / "c"
    cfg = EmitConfig(
        batch_events=500, retries=2, retry_backoff=0.001, checkpoint_path=str(ck), max_events=5000
    )
    with pytest.raises(ShapeError, match="after 2 retries"):
        EmitRunner(_plan(retail_engine), sink, cfg).run()
    doc = json.loads(ck.read_text())
    assert doc["offset"] == 1500 == sink.num_events and doc["complete"] is False
    # the next run continues from there; nothing is missing
    again = MemorySink()
    EmitRunner(
        _plan(retail_engine), again, EmitConfig(checkpoint_path=str(ck), max_events=5000)
    ).run()
    assert _seqs(sink) + _seqs(again) == list(range(5000))


def test_stop_request_checkpoints_on_shutdown(retail_engine, tmp_path: Path) -> None:
    ck = tmp_path / "c"
    runner: EmitRunner

    class Stopper(MemorySink):
        def send(self, batch: pa.RecordBatch) -> None:
            super().send(batch)
            if self.num_events >= 3000:
                runner.request_stop()

    first = Stopper()
    cfg = EmitConfig(
        batch_events=500, checkpoint_path=str(ck), checkpoint_every=10**9, checkpoint_seconds=10**9
    )
    runner = EmitRunner(_plan(retail_engine), first, cfg)
    report = runner.run()
    assert report.stopped_by == "stop-request" and not report.complete
    doc = json.loads(ck.read_text())
    assert doc["offset"] == first.num_events == report.end_offset  # only the shutdown wrote it
    assert report.checkpoints == 1
    second = MemorySink()
    r2 = EmitRunner(_plan(retail_engine), second, EmitConfig(checkpoint_path=str(ck))).run()
    assert r2.start_offset == doc["offset"] and r2.complete
    assert _seqs(first) + _seqs(second) == list(range(12500))


def test_periodic_checkpoints(retail_engine, tmp_path: Path) -> None:
    cfg = EmitConfig(batch_events=100, checkpoint_every=1000, checkpoint_path=str(tmp_path / "c"))
    report = EmitRunner(_plan(retail_engine), MemorySink(), cfg).run()
    assert report.checkpoints == 12500 // 1000 + 1  # every 1000 events, and at shutdown
    assert json.loads((tmp_path / "c").read_text())["complete"] is True


def test_a_completed_run_is_not_repeated_unless_fresh(retail_engine, tmp_path: Path) -> None:
    cfg = EmitConfig(checkpoint_path=str(tmp_path / "c"))
    EmitRunner(_plan(retail_engine), MemorySink(), cfg).run()
    sink = MemorySink()
    report = EmitRunner(_plan(retail_engine), sink, cfg).run()
    assert report.already_complete and report.events == 0 and sink.num_events == 0
    fresh = MemorySink()
    r = EmitRunner(
        _plan(retail_engine), fresh, EmitConfig(checkpoint_path=str(tmp_path / "c"), fresh=True)
    ).run()
    assert r.events == 12500 and r.start_offset == 0


def test_a_checkpoint_of_another_stream_is_refused(retail_engine, tmp_path: Path) -> None:
    cfg = EmitConfig(checkpoint_path=str(tmp_path / "c"), max_events=10)
    EmitRunner(_plan(retail_engine), MemorySink(), cfg).run()
    with pytest.raises(CheckpointError, match="different stream"):
        EmitRunner(_plan(retail_engine, out_of_order=0.1), MemorySink(), cfg).run()
    (tmp_path / "bad").write_text(json.dumps({"format": "other"}))
    with pytest.raises(CheckpointError, match="not an emit checkpoint"):
        EmitRunner(
            _plan(retail_engine), MemorySink(), EmitConfig(checkpoint_path=str(tmp_path / "bad"))
        ).run()


def test_all_tables_and_the_anomaly_report(retail_engine) -> None:
    from shape.streaming.emit import AnomalyInjector, ValueAnomalyMutator

    inj = AnomalyInjector(0.05, [ValueAnomalyMutator()], retail_engine.seed)
    sink = MemorySink()
    report = EmitRunner(EventPlan(retail_engine, anomaly=inj), sink).run()
    assert report.events == 21750
    assert {b.column(FIELD_TABLE)[0].as_py() for b in sink.batches} == set(retail_engine.order)
    assert 0.03 < report.anomalies_selected / report.events < 0.07
    assert report.anomalies_affected == {"value-anomaly": report.anomalies_selected}


def test_a_generator_error_is_raised_after_checkpointing(retail_engine, tmp_path: Path) -> None:
    class Broken(EventPlan):
        def block(self, table: str, index: int) -> Any:
            if index == 1:
                raise RuntimeError("boom")
            return super().block(table, index)

    ck = tmp_path / "c"
    sink = MemorySink()
    plan = Broken(retail_engine, tables=["order_line"])
    with pytest.raises(RuntimeError, match="boom"):
        EmitRunner(plan, sink, EmitConfig(checkpoint_path=str(ck))).run()
    assert json.loads(ck.read_text())["offset"] == sink.num_events == plan.block_rows
