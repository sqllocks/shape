"""Faults injected into a stream, and the answer key that lists them.

``FaultSink`` wraps the sink a run delivers to and, on top of the late and anomalous events the
plan makes (``--out-of-order``, ``--anomaly-fraction``), injects what delivery systems do:

* **duplicates** (``duplicate_fraction``): an event is delivered a second time, one to
  ``duplicate_window`` events later, as at-least-once delivery does. A consumer that keeps the
  first event of each ``(_shape_table, _shape_seq)`` key sees the original stream;
* **poison messages** (``poison_fraction``): an event is delivered cut off, so it is not valid
  JSON. Only sinks that send the JSON text accept it (a file, standard output, Kafka, Event Hubs).

Which events are chosen is a function of the seed and the event's key alone, so it is the same on
every run and after a restart. Nothing is changed in the events the plan made: a poisoned event's
key is intact, and its clean copy is never also a duplicate.

``AnswerKey`` is the log of every injection (``late`` and ``anomaly`` from the plan, ``duplicate``
and ``poison`` from here) as JSON lines, one record per injected event: ``kind``, ``table``,
``seq`` (the key is ``table/seq``) and details. A detector, a deduplicator or a dead-letter queue
can be scored against it. A resumed run writes the events after its checkpoint again, so a record
may appear twice: :func:`read_answer_key` returns each ``(kind, table, seq)`` once.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError
from shape.generation.rng import stream_key
from shape.streaming.emit.deadletter import RejectedEvents, Rejection
from shape.streaming.emit.formats import FIELD_POISON, FIELD_SEQ, FIELD_TABLE
from shape.streaming.emit.sinks import EventSink

KINDS = ("late", "anomaly", "duplicate", "poison")
_MASK = (1 << 64) - 1


class AnswerKey:
    """Appends injection records to a JSON-lines file (thread-safe; ``None`` path keeps them in
    memory only, for tests and hosts)."""

    def __init__(self, path: str | None = None, *, append: bool = False) -> None:
        self.path = path
        self.records: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._file = open(path, "a" if append else "w", encoding="utf-8") if path else None  # noqa: SIM115

    def record(
        self,
        kind: str,
        table: str,
        seqs: Sequence[int],
        per_event: Mapping[str, Sequence[Any]] | None = None,
        **detail: Any,
    ) -> None:
        """Log ``kind`` for events ``seqs`` of ``table``; ``per_event`` gives one value per event,
        ``detail`` the same value for all of them."""
        lines = []
        for i, seq in enumerate(seqs):
            row: dict[str, Any] = {
                "kind": kind,
                "table": table,
                "seq": int(seq),
                "key": f"{table}/{int(seq)}",
                **detail,
            }
            for name, values in (per_event or {}).items():
                row[name] = values[i]
            lines.append(row)
        if not lines:
            return
        with self._lock:
            self.records.extend(lines)
            if self._file is not None:
                self._file.write("".join(json.dumps(r, default=str) + "\n" for r in lines))
                self._file.flush()

    def close(self) -> None:
        with self._lock:
            if self._file is not None and not self._file.closed:
                self._file.close()


def read_answer_key(path: str) -> list[dict[str, Any]]:
    """The records of an answer-key file, each ``(kind, table, seq)`` once (the first wins)."""
    seen: set[tuple[str, str, int]] = set()
    out: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            ident = (row["kind"], row["table"], int(row["seq"]))
            if ident not in seen:
                seen.add(ident)
                out.append(row)
    return out


def _splitmix(x: np.ndarray[Any, np.dtype[np.uint64]]) -> np.ndarray[Any, np.dtype[np.uint64]]:
    with np.errstate(over="ignore"):
        x = x + np.uint64(0x9E3779B97F4A7C15)
        x = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
        x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
        return x ^ (x >> np.uint64(31))


def event_uniform(
    seed: int, label: str, table: str, seq: np.ndarray[Any, Any]
) -> np.ndarray[Any, Any]:
    """A number in ``[0, 1)`` for each event key ``(table, seq)``: a function of the seed, the
    label and the key alone."""
    key = np.uint64(stream_key(seed, table, "_fault", label) & _MASK)
    raw = _splitmix(seq.astype(np.uint64) ^ key)
    return (raw >> np.uint64(11)).astype(np.float64) * 2.0**-53


class FaultSink:
    """Duplicate and poison events on their way to ``sink`` (see the module docstring)."""

    def __init__(
        self,
        sink: EventSink,
        *,
        seed: int,
        duplicate_fraction: float = 0.0,
        duplicate_window: int = 1000,
        poison_fraction: float = 0.0,
        answer_key: AnswerKey | None = None,
    ) -> None:
        for name, value in (("duplicate", duplicate_fraction), ("poison", poison_fraction)):
            if not 0.0 <= value <= 1.0:
                raise ShapeError(f"the {name} fraction must be between 0 and 1")
        if duplicate_window < 1:
            raise ShapeError("the duplicate window must be at least 1")
        if poison_fraction > 0 and not getattr(sink, "accepts_poison", False):
            raise ShapeError(
                "--poison-fraction needs a sink that sends the JSON text (a file, standard "
                "output, Kafka, Event Hubs): this sink stores typed values"
            )
        self.sink = sink
        self.seed = seed
        self.duplicate_fraction = duplicate_fraction
        self.duplicate_window = duplicate_window
        self.poison_fraction = poison_fraction
        self.answer_key = answer_key
        self._sent = 0  # events delivered (not counting the copies)
        self._pending: list[tuple[int, pa.RecordBatch]] = []  # (due after this many events, copy)
        self._last: pa.RecordBatch | None = None  # a retried batch is not injected twice
        self._last_marked: pa.RecordBatch | None = None
        self._poisoned: list[tuple[str, np.ndarray[Any, Any]]] = []

    @property
    def accepts_poison(self) -> bool:
        return bool(getattr(self.sink, "accepts_poison", False))

    def __getattr__(self, name: str) -> Any:
        return getattr(self.sink, name)

    def _by_table(self, batch: pa.RecordBatch) -> Iterator[tuple[str, np.ndarray[Any, Any]]]:
        tables = batch.column(FIELD_TABLE).to_pylist()
        names = list(dict.fromkeys(tables))
        arr = np.asarray(tables, dtype=object)
        for t in names:
            yield t, np.flatnonzero(arr == t)

    def _plan(self, batch: pa.RecordBatch) -> pa.RecordBatch:
        """The batch to send, with poison marks, and the copies queued."""
        n = batch.num_rows
        seq = np.asarray(batch.column(FIELD_SEQ).to_numpy(zero_copy_only=False), dtype=np.int64)
        poison = np.zeros(n, dtype=bool)
        poisoned: list[tuple[str, np.ndarray[Any, Any]]] = []
        for table, idx in self._by_table(batch):
            s = seq[idx]
            if self.poison_fraction > 0:
                hit = event_uniform(self.seed, "poison", table, s) < self.poison_fraction
                poison[idx[hit]] = True
                if hit.any():
                    poisoned.append((table, s[hit]))
            if self.duplicate_fraction > 0:
                chosen = event_uniform(self.seed, "dup", table, s) < self.duplicate_fraction
                chosen &= ~poison[idx]
                if chosen.any():
                    delay = 1 + np.floor(
                        event_uniform(self.seed, "dupdelay", table, s[chosen])
                        * self.duplicate_window
                    ).astype(np.int64)
                    take = idx[chosen]
                    for row, wait in zip(take, delay, strict=True):
                        self._pending.append((self._sent + n + int(wait), batch.slice(int(row), 1)))
        self._poisoned = poisoned
        if poison.any():
            return batch.append_column(FIELD_POISON, pa.array(poison))
        return batch

    def send(self, batch: pa.RecordBatch) -> None:
        if self.poison_fraction <= 0 and self.duplicate_fraction <= 0:
            self.sink.send(batch)
            return
        if batch is not self._last:  # a retry of the same batch reuses the plan
            self._last = batch
            self._last_marked = self._plan(batch)
        assert self._last_marked is not None
        self.sink.send(self._last_marked)
        self._sent += batch.num_rows
        if self.answer_key is not None:
            for table, seqs in self._poisoned:
                self.answer_key.record("poison", table, seqs.tolist())
        self._poisoned = []
        self._release()

    def _release(self, *, everything: bool = False) -> None:
        due = [p for p in self._pending if everything or p[0] <= self._sent]
        self._pending = [p for p in self._pending if not (everything or p[0] <= self._sent)]
        for _, copy in due:
            self.sink.send(copy)
            if self.answer_key is not None:
                self.answer_key.record(
                    "duplicate",
                    copy.column(FIELD_TABLE)[0].as_py(),
                    [copy.column(FIELD_SEQ)[0].as_py()],
                    delivered_after_events=self._sent,
                )

    def flush(self) -> None:
        self.sink.flush()

    def close(self) -> None:
        try:
            self._release(everything=True)
        finally:
            try:
                self.sink.close()
            finally:
                if self.answer_key is not None:
                    self.answer_key.close()


class FanOutSink:
    """Every batch to every sink. A sink that fails is retried alone: the sinks that already took
    the batch are not sent it again when the runtime retries ``send``."""

    def __init__(self, sinks: Sequence[EventSink]) -> None:
        if not sinks:
            raise ValueError("a fan-out needs at least one sink")
        self.sinks = list(sinks)
        self._current: pa.RecordBatch | None = None
        self._done: set[int] = set()
        self._rejected: list[Rejection] = []

    @property
    def accepts_poison(self) -> bool:
        return all(getattr(s, "accepts_poison", False) for s in self.sinks)

    def send(self, batch: pa.RecordBatch) -> None:
        if batch is not self._current:
            self._current, self._done, self._rejected = batch, set(), []
        first: BaseException | None = None
        for i, sink in enumerate(self.sinks):
            if i in self._done:
                continue
            try:
                sink.send(batch)
                self._done.add(i)
            except RejectedEvents as exc:
                # it delivered everything else: done, and its rejections are raised together
                self._done.add(i)
                self._rejected.extend(exc.rejections)
            except BaseException as exc:
                first = first or exc
        if first is not None:
            raise first
        rejected, self._rejected = self._rejected, []
        self._current, self._done = None, set()
        if rejected:
            raise RejectedEvents(rejected)

    def _each(self, name: str) -> None:
        first: BaseException | None = None
        for sink in self.sinks:
            try:
                getattr(sink, name)()
            except BaseException as exc:
                first = first or exc
        if first is not None:
            raise first

    def flush(self) -> None:
        self._each("flush")

    def close(self) -> None:
        self._each("close")
