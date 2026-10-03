"""Dead-letter routing (W2-09): events a destination refuses go elsewhere instead of stopping
the run.

An emitter that gets a **non-retryable per-message error** from its destination (a message too
large, a record the format cannot hold), or that cannot encode an event in the chosen
``--event-format``, delivers every other event of the batch and then raises
:class:`RejectedEvents` with the keys and reasons. It is not an ``OSError``: the runtime never
retries it. Without a dead-letter destination it stops the run (exit 2); with ``--dead-letter URI``
:class:`DeadLetterSink` writes one record per rejected event to that destination and the run goes
on.

**Record format** (``format: "shape-dead-letter"``, ``version: 1``), one JSON object per line:
``key`` (``<table>/<seq>``), ``table``, ``seq``, ``reason``, ``destination`` (the URI that
rejected the event, password redacted), ``attempts`` (deliveries of its batch up to the
rejection), ``at`` (UTC, ISO 8601 with microseconds and ``Z``) and ``body``: the message the
destination refused as text, or base64 with ``body_encoding: "base64"`` when it is not UTF-8
(``body_encoding`` is absent for text). Where the destination is an emitter the record also holds
``_shape_table`` and ``_shape_seq`` (the same values as ``table`` and ``seq``), which a transport
turns into its message key, so the key of a dead-letter message is the original event's D-12 key;
a transport with headers also carries the header ``shape-dead-letter-reason``.

**Checkpoint rule.** ``DeadLetterSink.send`` returns only after the dead-letter destination
acknowledged the records, so the runtime's checkpoint never moves past a dead-lettered event
before that. A dead-letter write that fails transiently is retried by the runtime without sending
the batch to the primary destination again. Delivery is at-least-once, so after a crash an event
may be dead-lettered twice: ``key`` removes the repeat.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError
from shape.plugins.schemes import redact
from shape.streaming.emit.formats import (
    FIELD_DEAD_REASON,
    FIELD_SEQ,
    FIELD_TABLE,
    encode_events,
)
from shape.streaming.emit.sinks import EventSink

DEAD_LETTER_FORMAT = "shape-dead-letter"
DEAD_LETTER_VERSION = 1
DEAD_LETTER_TABLE = "dead_letter"  # the table a table sink stores the records in
MAX_REASONS = 50  # distinct reasons the report counts; the rest are counted as "other"


@dataclass(frozen=True, slots=True)
class Rejection:
    """One refused event: its key, why, and (when the emitter knows it) the message it sent."""

    key: str
    reason: str
    body: bytes | None = None  # None: the flat JSON event
    destination: str = ""  # filled in by the sink that owns the emitter


class RejectedEvents(ShapeError):
    """Events the destination refused for good. Raised by an emitter *after* every other event of
    the batch was delivered (and acknowledged); never retried by the runtime."""

    def __init__(self, rejections: Iterable[Rejection | tuple[str, str]]) -> None:
        items = [r if isinstance(r, Rejection) else Rejection(r[0], r[1]) for r in rejections]
        if not items:
            raise ValueError("RejectedEvents needs at least one rejection")
        self.rejections = items
        first = items[0]
        n = len(items)
        super().__init__(
            f"the destination rejected {n} event{'s' if n != 1 else ''} "
            f"(first {first.key}: {first.reason}); use --dead-letter URI to route them elsewhere"
        )

    @property
    def keys(self) -> list[str]:
        return [r.key for r in self.rejections]

    @property
    def reasons(self) -> list[str]:
        return [r.reason for r in self.rejections]


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class DeadLetterSink:
    """``sink`` with a dead-letter destination ``dlq`` (a sink of flat events) behind it.

    ``destination`` names the primary destination in the records (redacted here);
    ``max_dead_letter`` stops the run, after the batch in hand, once more than that many events
    were dead-lettered (the runner's ``request_stop`` is bound by ``EmitRunner(dead_letter=...)``).
    ``table_target`` is true when ``dlq`` stores typed rows in tables (a table sink): the records
    then go to the table ``dead_letter``.
    """

    def __init__(
        self,
        sink: EventSink,
        dlq: EventSink,
        *,
        destination: str,
        max_dead_letter: int | None = None,
        table_target: bool = False,
        now: Callable[[], dt.datetime] = _utc_now,
    ) -> None:
        if max_dead_letter is not None and max_dead_letter < 0:
            raise ShapeError("--max-dead-letter must be 0 or more")
        self.sink = sink
        self.dlq = dlq
        self.destination = redact(destination)
        self.max_dead_letter = max_dead_letter
        self.table_target = table_target
        self._now = now
        self.counts: dict[str, int] = {}
        self.total = 0
        self.limit_exceeded = False
        self._stop: Callable[[str], None] | None = None
        self._current: pa.RecordBatch | None = None
        self._attempts = 0
        self._pending: list[pa.RecordBatch] | None = None
        self._pending_rows = 0

    @property
    def accepts_poison(self) -> bool:
        return bool(getattr(self.sink, "accepts_poison", False))

    def __getattr__(self, name: str) -> Any:
        if name in ("sink", "dlq"):
            raise AttributeError(name)
        return getattr(self.sink, name)

    def bind(self, request_stop: Callable[[str], None]) -> None:
        """The runner's stop request (called with the reason) for ``max_dead_letter``."""
        self._stop = request_stop

    # ---- delivery -----------------------------------------------------------------------

    def send(self, batch: pa.RecordBatch) -> None:
        if batch is not self._current:  # a retry of the same batch continues where it was
            self._current, self._attempts, self._pending = batch, 0, None
        if self._pending is None:
            self._attempts += 1
            try:
                self.sink.send(batch)
            except RejectedEvents as exc:
                self._pending = self._records(batch, exc, self._attempts)
                self._pending_rows = len(exc.rejections)
                self._count(exc)
            else:
                self._current = None
                return
        while self._pending:
            self.dlq.send(self._pending[0])  # acknowledged before the next, and before we return
            self._pending.pop(0)
        self._pending = None
        self._current = None
        if self.max_dead_letter is not None and self.total > self.max_dead_letter:
            self.limit_exceeded = True
            if self._stop is not None:
                self._stop("dead-letter-limit")

    def _count(self, exc: RejectedEvents) -> None:
        for r in exc.rejections:
            reason = (
                r.reason if r.reason in self.counts or len(self.counts) < MAX_REASONS else "other"
            )
            self.counts[reason] = self.counts.get(reason, 0) + 1
            self.total += 1

    def _records(
        self, batch: pa.RecordBatch, exc: RejectedEvents, attempts: int
    ) -> list[pa.RecordBatch]:
        keys = [
            f"{t}/{s}"
            for t, s in zip(
                batch.column(FIELD_TABLE).to_pylist(),
                batch.column(FIELD_SEQ).to_pylist(),
                strict=True,
            )
        ]
        row_of = {k: i for i, k in reversed(list(enumerate(keys)))}  # the first row of a key
        at = self._now().astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        text: list[dict[str, Any]] = []
        binary: list[dict[str, Any]] = []
        for r in exc.rejections:
            if r.key not in row_of:
                raise ShapeError(
                    f"the destination rejected {r.key}, which is not in the batch it was sent"
                )
            i = row_of[r.key]
            table = batch.column(FIELD_TABLE)[i].as_py()
            seq = batch.column(FIELD_SEQ)[i].as_py()
            body = r.body if r.body is not None else encode_events(batch.slice(i, 1))[0].body
            rec: dict[str, Any] = {
                "format": DEAD_LETTER_FORMAT,
                "version": DEAD_LETTER_VERSION,
                "key": r.key,
                "table": table,
                "seq": seq,
                "reason": r.reason,
                "destination": redact(r.destination) if r.destination else self.destination,
                "attempts": attempts,
                "at": at,
            }
            try:
                rec["body"] = body.decode("utf-8")
                text.append(rec)
            except UnicodeDecodeError:
                rec["body"] = base64.b64encode(body).decode("ascii")
                rec["body_encoding"] = "base64"
                binary.append(rec)
        return [self._batch(group) for group in (text, binary) if group]

    def _batch(self, recs: Sequence[dict[str, Any]]) -> pa.RecordBatch:
        columns = {name: [r[name] for r in recs] for name in recs[0]}
        types = {"version": pa.int64(), "seq": pa.int64(), "attempts": pa.int64()}
        arrays = {n: pa.array(v, types.get(n, pa.string())) for n, v in columns.items()}
        arrays[FIELD_TABLE] = pa.array(
            [DEAD_LETTER_TABLE] * len(recs) if self.table_target else columns["table"], pa.string()
        )
        arrays[FIELD_SEQ] = pa.array(columns["seq"], pa.int64())
        arrays[FIELD_DEAD_REASON] = pa.array(columns["reason"], pa.string())
        return pa.RecordBatch.from_arrays(list(arrays.values()), names=list(arrays))

    # ---- lifecycle ----------------------------------------------------------------------

    def flush(self) -> None:
        first: BaseException | None = None
        for sink in (self.sink, self.dlq):
            try:
                sink.flush()
            except BaseException as exc:
                first = first or exc
        if first is not None:
            raise first

    def close(self) -> None:
        first: BaseException | None = None
        for sink in (self.sink, self.dlq):
            try:
                sink.close()
            except BaseException as exc:
                first = first or exc
        if first is not None:
            raise first


def read_dead_letters(path: str) -> Iterator[dict[str, Any]]:
    """The records of a dead-letter JSON-lines file, checked for ``format`` and ``version``: a
    record of another format, or of a newer version than this Shape reads, is an error."""
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            if not isinstance(rec, dict) or rec.get("format") != DEAD_LETTER_FORMAT:
                raise ShapeError(f"{path}: not a {DEAD_LETTER_FORMAT} record")
            version = rec.get("version")
            if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                raise ShapeError(f"{path}: the version must be an integer of 1 or more")
            if version > DEAD_LETTER_VERSION:
                raise ShapeError(
                    f"{path}: this record is version {version}, which is newer than the version "
                    f"{DEAD_LETTER_VERSION} this Shape reads; upgrade Shape to read it"
                )
            yield rec
