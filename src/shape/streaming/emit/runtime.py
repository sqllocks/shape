"""The emitter runtime (P5-01): pace, deliver, checkpoint.

::

    EventPlan (generator thread) --bounded queue--> runner (pacing, delivery, checkpoint) --> sink

* **Rate.** ``realtime=False`` (the default) delivers as fast as the sink takes events.
  ``realtime=True`` paces to ``rate`` events per second with optional bursts
  (:mod:`shape.streaming.emit.rate`), sleeping until each batch's absolute due time. If the sink
  is too slow the run falls behind schedule and never drops or skips an event; the report gives
  the worst lag.
* **Virtual clock.** ``speed`` paces by the events' *event time* instead of a rate: an event stamped
  ``t`` seconds after the first is due ``t / speed`` wall-clock seconds after the start, so
  ``speed=60`` replays an hour of events in a minute (:class:`~shape.streaming.emit.rate.
  VirtualClock`). It needs events with an event time (``shape stream`` delivers them in time
  order). ``max_rate`` is a hard cap in events per second that holds whatever else is set: no
  second of the run delivers more than that on average from the start.
* **Backpressure.** The queue between the generator and the runner is bounded
  (``queue_batches``): a slow sink blocks the runner, the queue fills, and the generator waits.
* **Limits.** ``max_events`` is a position in the sequence (the run stops with that many events
  delivered in all, counting those a previous run delivered); ``duration`` is wall-clock seconds
  of this run.
* **At-least-once.** A batch counts as delivered when the sink's ``send`` returns, and only then
  can a checkpoint move past it. A run killed at any point is resumed from the last checkpoint, so
  events after it may be sent again; every event carries the D-12 key ``(_shape_table,
  _shape_seq)``, and a consumer that keeps the first event of each key sees the same sequence as
  an uninterrupted run.
* **Checkpoint.** An atomic JSON document (offset, plan fingerprint, completion) written every
  ``checkpoint_every`` events or ``checkpoint_seconds``, and always on shutdown (end of the data,
  a limit, a stop request, or an error).
* **Pacing is never blocked by housekeeping (realtime).** Two things used to be able to stall the
  pacing thread for tens of milliseconds, which shows up as lag and as a short second: a full
  garbage collection (its cost grows with every object the process holds, so a long-lived host
  such as a test run or a notebook pays far more than a fresh ``shape emit``), and the checkpoint
  write (an ``fsync``). A realtime run therefore freezes the objects that exist when it starts
  (``gc.freeze``: a collection then scans only what the run itself allocates) and writes
  checkpoints on a writer thread. The writer only ever persists an offset the pacing thread has
  already delivered *and flushed*, so the at-least-once guarantee is unchanged; the final
  checkpoint is still written synchronously before ``run`` returns.
"""

from __future__ import annotations

import gc
import queue
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError
from shape.streaming.checkpoint import CheckpointError, FileCheckpointStore
from shape.streaming.emit.anomaly import AnomalyInjector
from shape.streaming.emit.formats import FIELD_TIME
from shape.streaming.emit.rate import Burst, RateCap, RateSchedule, VirtualClock
from shape.streaming.emit.sinks import EventSink
from shape.streaming.emit.source import EventBlock

CHECKPOINT_FORMAT = "shape-emit-v1"
_END = object()

_gc_lock = threading.Lock()
_gc_holders = 0
_gc_owned = False


@contextmanager
def _gc_frozen() -> Iterator[None]:
    """Keep full garbage collections cheap while a realtime run is pacing.

    Freezes the objects that exist now (they stay alive and are scanned again only after
    ``gc.unfreeze``); runs may overlap, and a freeze the caller made itself is left alone."""
    global _gc_holders, _gc_owned
    with _gc_lock:
        if _gc_holders == 0:
            _gc_owned = gc.get_freeze_count() == 0
            if _gc_owned:
                gc.freeze()
        _gc_holders += 1
    try:
        yield
    finally:
        with _gc_lock:
            _gc_holders -= 1
            if _gc_holders == 0 and _gc_owned:
                gc.unfreeze()
                _gc_owned = False


class _CheckpointWriter:
    """Persists the newest submitted offset on its own thread (latest wins), so a slow ``fsync``
    delays the writer and never the pacing. A failed write is raised by the next ``submit`` or by
    ``close``."""

    def __init__(self, write: Callable[[int], None]) -> None:
        self._write = write
        self._cond = threading.Condition()
        self._pending: int | None = None
        self._closed = False
        self._error: BaseException | None = None
        self._thread = threading.Thread(target=self._loop, name="shape-emit-ckpt", daemon=True)
        self._thread.start()

    def submit(self, offset: int) -> None:
        with self._cond:
            if self._error is not None:
                raise self._error
            self._pending = offset
            self._cond.notify()

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify()
        self._thread.join()
        if self._error is not None:
            raise self._error

    def _loop(self) -> None:
        while True:
            with self._cond:
                while self._pending is None and not self._closed:
                    self._cond.wait()
                offset, self._pending = self._pending, None
                if offset is None:
                    return
            try:
                self._write(offset)
            except BaseException as exc:
                with self._cond:
                    self._error = exc
                    self._pending = None
                return


class EventSequence(Protocol):
    """What the runner needs of a plan: a counted, resumable sequence of event blocks.
    ``EventPlan`` is one (a schema's rows); the simulation plugin's stream emitter is another
    (the rows of tables it is given)."""

    total_events: int
    anomaly: AnomalyInjector | None

    def fingerprint(self) -> str: ...

    def blocks(self, offset: int = 0) -> Iterator[EventBlock]: ...


@dataclass
class EmitConfig:
    realtime: bool = False
    rate: float = 100.0
    bursts: tuple[Burst, ...] = ()
    max_events: int | None = None
    duration: float | None = None
    batch_events: int | None = None  # events per ``send``; default: see ``effective_batch``
    queue_batches: int | None = None  # default: 8, or about a second of batches when realtime
    checkpoint_path: str | None = None
    checkpoint_every: int = 10_000
    checkpoint_seconds: float = 1.0
    fresh: bool = False  # ignore an existing checkpoint
    retries: int = 3
    retry_backoff: float = 0.1
    speed: float | None = None  # virtual clock: event time passes this many times faster
    max_rate: float | None = None  # hard cap, events per second

    def effective_queue(self) -> int:
        if self.queue_batches is not None:
            return self.queue_batches
        if self.realtime:
            # A second of batches: the generator may stall for a block without delaying the pacing.
            return max(8, -(-int(self.rate) // self.effective_batch()))
        return 8

    def effective_batch(self) -> int:
        if self.batch_events is not None:
            return self.batch_events
        if self.realtime:
            # About 100 batches per second, so the pacing is fine-grained but sends are not tiny.
            return max(1, min(1000, int(self.rate // 100)))
        if self.max_rate is not None:
            return max(1, min(1000, int(self.max_rate // 100)))
        if self.speed is not None:
            return 100
        return 1000


@dataclass
class EmitReport:
    events: int = 0  # delivered by this run
    start_offset: int = 0
    end_offset: int = 0
    total_events: int = 0
    complete: bool = False
    stopped_by: str = ""  # complete | max-events | duration | stop-request | error
    elapsed: float = 0.0
    rate: float = 0.0  # delivered events per second between first and last delivery
    max_queue_depth: int = 0  # the most batches waiting between the generator and the sink
    max_lag: float = 0.0  # realtime: the most seconds a batch was sent after its due time
    per_second: list[int] = field(default_factory=list)  # events delivered in each second
    checkpoints: int = 0
    retries: int = 0
    anomalies_selected: int = 0
    anomalies_affected: dict[str, int] = field(default_factory=dict)
    already_complete: bool = False
    virtual_span: float = 0.0  # speed: seconds of event time replayed

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class EmitRunner:
    def __init__(
        self,
        plan: EventSequence,
        sink: EventSink,
        config: EmitConfig | None = None,
        *,
        sleep_until: Callable[[threading.Event, float], None] | None = None,
    ) -> None:
        self.plan = plan
        self.sink = sink
        self.config = config or EmitConfig()
        cfg = self.config
        if cfg.queue_batches is not None and cfg.queue_batches < 1:
            raise ValueError("queue_batches must be at least 1")
        if cfg.batch_events is not None and cfg.batch_events < 1:
            raise ValueError("batch_events must be at least 1")
        if cfg.max_events is not None and cfg.max_events < 0:
            raise ValueError("max_events must be 0 or more")
        if cfg.duration is not None and cfg.duration < 0:
            raise ValueError("duration must be 0 or more")
        if cfg.speed is not None and cfg.realtime:
            raise ValueError("speed paces by event time and --realtime by rate: choose one")
        self.schedule = RateSchedule(cfg.rate, cfg.bursts) if cfg.realtime else None
        self.clock = VirtualClock(cfg.speed) if cfg.speed is not None else None
        self.cap = RateCap(cfg.max_rate) if cfg.max_rate is not None else None
        self.paced = self.schedule is not None or self.clock is not None or self.cap is not None
        self._stop = threading.Event()
        self._store = FileCheckpointStore(cfg.checkpoint_path) if cfg.checkpoint_path else None
        self._sleep_until = sleep_until

    # ---- control ------------------------------------------------------------------------

    def request_stop(self) -> None:
        """Ask the run to finish: the batch in hand is delivered, a checkpoint is written, and
        ``run`` returns. Safe to call from a signal handler or another thread."""
        self._stop.set()

    @property
    def limit(self) -> int:
        cap = self.plan.total_events
        m = self.config.max_events
        return cap if m is None else min(cap, m)

    # ---- checkpoint ---------------------------------------------------------------------

    def load_offset(self) -> tuple[int, bool]:
        """``(offset, complete)`` a previous run left (``(0, False)`` for none, or ``fresh``)."""
        if self._store is None or self.config.fresh:
            return 0, False
        doc = self._store.load_document()
        if doc is None:
            return 0, False
        if doc.get("format") != CHECKPOINT_FORMAT:
            raise CheckpointError(f"{self._store.path} is not an emit checkpoint")
        if doc.get("fingerprint") != self.plan.fingerprint():
            raise CheckpointError(
                f"{self._store.path} belongs to a different stream (another schema, seed, scale "
                "or option set); use --fresh to start over"
            )
        offset = int(doc["offset"])
        if offset < 0 or offset > self.plan.total_events:
            raise CheckpointError(f"{self._store.path} holds an offset outside the stream")
        return offset, bool(doc.get("complete", False))

    def _save(self, offset: int, complete: bool, report: EmitReport) -> None:
        if self._store is None:
            return
        self._store.save_document(
            {
                "format": CHECKPOINT_FORMAT,
                "fingerprint": self.plan.fingerprint(),
                "offset": offset,
                "total": self.plan.total_events,
                "complete": complete,
            }
        )
        report.checkpoints += 1

    # ---- the run ------------------------------------------------------------------------

    def _producer(self, q: queue.Queue[Any], offset: int, errors: list[BaseException]) -> None:
        step = self.config.effective_batch()
        limit = self.limit
        try:
            for block in self.plan.blocks(offset):
                if block.offset >= limit:
                    break
                batch = block.batch
                if block.offset + batch.num_rows > limit:
                    batch = batch.slice(0, limit - block.offset)
                for i in range(0, batch.num_rows, step):
                    if not self._put(q, (block.offset + i, batch.slice(i, step))):
                        return
        except BaseException as exc:
            errors.append(exc)
        finally:
            self._put(q, _END)

    def _put(self, q: queue.Queue[Any], item: Any) -> bool:
        while True:
            try:
                q.put(item, timeout=0.05)
                return True
            except queue.Full:
                if self._stop.is_set():
                    return False

    def _wait_until(self, deadline: float) -> None:
        if self._sleep_until is not None:
            self._sleep_until(self._stop, deadline)
            return
        remaining = deadline - time.perf_counter()
        if remaining > 0:
            self._stop.wait(remaining)

    def _send(self, batch: pa.RecordBatch, report: EmitReport) -> None:
        cfg = self.config
        attempt = 0
        while True:
            try:
                self.sink.send(batch)
                return
            except (OSError, ConnectionError, TimeoutError) as exc:
                attempt += 1
                if attempt > cfg.retries:
                    raise ShapeError(f"delivery failed after {cfg.retries} retries: {exc}") from exc
                report.retries += 1
                time.sleep(cfg.retry_backoff * (2 ** (attempt - 1)))

    def _due(self, batch: pa.RecordBatch, sent: int) -> float | None:
        """Seconds after the start at which ``batch`` may be sent (``sent`` events have gone), or
        ``None`` when the run is not paced."""
        if not self.paced:
            return None
        due = 0.0
        if self.schedule is not None:
            due = self.schedule.due_time(sent)
        if self.clock is not None:
            due = max(due, self.clock.due(_event_seconds(batch)))
        if self.cap is not None:
            due = max(due, self.cap.due(sent + batch.num_rows))
        return due

    def run(self) -> EmitReport:
        if not self.paced:
            return self._run()
        with _gc_frozen():
            return self._run()

    def _run(self) -> EmitReport:
        cfg = self.config
        report = EmitReport(total_events=self.plan.total_events)
        offset, was_complete = self.load_offset()
        report.start_offset = report.end_offset = offset
        limit = self.limit
        if was_complete and offset >= limit:
            report.complete = report.already_complete = True
            report.stopped_by = "complete"
            self.sink.close()
            return report
        if offset >= limit:
            report.complete = True
            report.stopped_by = "max-events" if limit < self.plan.total_events else "complete"
            self._save(offset, report.complete, report)
            self.sink.close()
            return report

        q: queue.Queue[Any] = queue.Queue(maxsize=cfg.effective_queue())
        errors: list[BaseException] = []
        writer: _CheckpointWriter | None = None
        if self._store is not None and self.paced:
            writer = _CheckpointWriter(lambda off: self._save(off, False, report))
        producer = threading.Thread(
            target=self._producer, args=(q, offset, errors), name="shape-emit-gen", daemon=True
        )
        producer.start()
        failure: BaseException | None = None
        stopped_by = "complete"
        first: float | None = None
        first_n = 0
        last = 0.0
        since_checkpoint = 0
        checkpoint_time = time.perf_counter()
        delivered = offset
        try:
            t0: float | None = None
            while True:
                if self._stop.is_set():
                    stopped_by = "stop-request"
                    break
                try:
                    item = q.get(timeout=0.05)
                except queue.Empty:
                    continue
                if item is _END:
                    break
                start, batch = item
                report.max_queue_depth = max(report.max_queue_depth, q.qsize() + 1)
                n = int(batch.num_rows)
                now = time.perf_counter()
                if t0 is None:
                    t0 = now
                if cfg.duration is not None and now - t0 >= cfg.duration:
                    stopped_by = "duration"
                    break
                rel = self._due(batch, delivered - offset)
                if rel is not None:
                    due = t0 + rel
                    if cfg.duration is not None and due - t0 >= cfg.duration:
                        self._wait_until(t0 + cfg.duration)
                        stopped_by = "stop-request" if self._stop.is_set() else "duration"
                        break
                    self._wait_until(due)
                    if self._stop.is_set():
                        stopped_by = "stop-request"
                        break
                    report.max_lag = max(report.max_lag, time.perf_counter() - due)
                self._send(batch, report)
                now = time.perf_counter()
                if first is None:
                    first = now
                    first_n = n
                last = now
                delivered = start + n
                report.events += n
                second = int(now - t0)
                if len(report.per_second) <= second:
                    report.per_second.extend([0] * (second + 1 - len(report.per_second)))
                report.per_second[second] += n
                since_checkpoint += n
                if (
                    since_checkpoint >= cfg.checkpoint_every
                    or now - checkpoint_time >= cfg.checkpoint_seconds
                ):
                    self.sink.flush()
                    if writer is not None:
                        writer.submit(delivered)
                    else:
                        self._save(delivered, False, report)
                    since_checkpoint = 0
                    checkpoint_time = now
        except BaseException as exc:
            failure = exc
            stopped_by = "error"
        finally:
            self._stop.set()
            while producer.is_alive():
                try:
                    q.get(timeout=0.05)
                except queue.Empty:
                    pass
            producer.join()
            try:
                if writer is not None:
                    writer.close()
            except BaseException as exc:
                failure = failure or exc
                stopped_by = "error"
            try:
                self.sink.flush()
            except BaseException as exc:
                failure = failure or exc
                stopped_by = "error"
            report.end_offset = delivered
            if self.clock is not None:
                report.virtual_span = self.clock.span
            report.complete = failure is None and delivered >= limit
            if errors and stopped_by != "error":
                stopped_by = "error"
            if report.complete and stopped_by != "error":
                stopped_by = "max-events" if limit < self.plan.total_events else "complete"
            report.stopped_by = stopped_by
            if first is not None and last > first:
                report.rate = (report.events - first_n) / (last - first)
            report.elapsed = (last - first) if first is not None else 0.0
            if self.plan.anomaly is not None:
                report.anomalies_selected = self.plan.anomaly.stats.rows_selected
                report.anomalies_affected = dict(self.plan.anomaly.stats.rows_affected)
            try:
                self._save(delivered, report.complete, report)
            finally:
                self.sink.close()
        if failure is None and errors:
            raise errors[0]
        if failure is not None:
            raise failure
        return report


def _event_seconds(batch: pa.RecordBatch) -> float | None:
    """The earliest event time of ``batch`` as seconds since the epoch (UTC for a time without a
    zone), or ``None`` when no event has one. A batch without the event-time column cannot be
    paced by a virtual clock."""
    import datetime as dt

    import pyarrow.compute as pc  # type: ignore[import-untyped]

    if FIELD_TIME not in batch.schema.names:
        raise ShapeError(
            "--speed paces by event time, and these events have none: the table needs a date or "
            "timestamp column"
        )
    column = batch.column(FIELD_TIME)
    if column.null_count == len(column):
        return None
    earliest = pc.min(column.cast(pa.timestamp("us"))).as_py()
    if earliest.tzinfo is None:
        earliest = earliest.replace(tzinfo=dt.UTC)
    return float(earliest.timestamp())
