"""
Connector qualification primitives: ordering, dedupe, partition checkpoints, reconnect and
replay.
"""

from __future__ import annotations

import inspect
import itertools
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ConnectorRecord:
    partition: str
    offset: int
    value: object
    message_id: str | None = None


class PartitionCheckpointStore:
    def __init__(self) -> None:
        self.offsets: dict[str, int] = {}

    def committed(self, partition: object) -> int:
        return self.offsets.get(str(partition), -1)

    def commit(self, partition: object, offset: int) -> None:
        p = str(partition)
        o = int(offset)
        if o > self.committed(p):
            self.offsets[p] = o


class ExactlyOnceProjector:
    """Hand each record to a handler once: records at or below the partition's committed offset
    are stale, and a record whose ``message_id`` was already handled is a duplicate.

    ``ids`` keeps every ``message_id`` handled, so memory grows with the number of distinct ids.
    It is not bounded on purpose: forgetting an id would let its duplicate through again.
    """

    def __init__(self, store: PartitionCheckpointStore | None = None) -> None:
        self.store = store or PartitionCheckpointStore()
        self.ids: set[str] = set()

    def process(
        self, records: Iterable[ConnectorRecord], handler: Callable[[object], object]
    ) -> dict[str, Any]:
        accepted = duplicates = stale = 0
        for r in records:
            if r.offset <= self.store.committed(r.partition):
                stale += 1
                continue
            if r.message_id is not None and r.message_id in self.ids:
                duplicates += 1
                self.store.commit(r.partition, r.offset)
                continue
            handler(r.value)
            if r.message_id is not None:
                self.ids.add(r.message_id)
            self.store.commit(r.partition, r.offset)
            accepted += 1
        return {
            "accepted": accepted,
            "duplicates": duplicates,
            "stale": stale,
            "offsets": dict(self.store.offsets),
        }


class _NotAnOffsetItem(TypeError):
    """A resumable connection yielded something other than ``(offset, batch)``."""


def _takes_start(connect: Callable[..., Any]) -> bool:
    try:
        params = inspect.signature(connect).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.VAR_POSITIONAL) for p in params
    )


_MAX_BACKOFF = 30.0  # seconds: the longest pause between two reconnects


def reconnecting_batches(
    connect: Callable[..., Iterable[Any]],
    max_attempts: int = 5,
    *,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    backoff: float = 0.5,
    sleep: Callable[[float], object] = time.sleep,
) -> Iterator[Any]:
    """Yield what ``connect`` yields, reconnecting after a failure without replaying anything.

    ``connect(start)`` returns an iterator of ``(offset, batch)``, as a ``StreamSource.read``
    does. ``start`` is ``None`` the first time and afterwards the offset of the last item this
    function yielded, so the next connection resumes right after it (S4: it used to call
    ``connect()`` again, which restarted at the beginning and yielded every batch a second time).
    A ``connect()`` that takes no argument is accepted for a source that always starts from the
    beginning: the items already yielded are skipped by position.

    ``max_attempts`` counts consecutive failures; a connection that delivers an item resets it.
    Before each reconnect it waits ``backoff`` seconds, doubled for each further consecutive
    failure and capped at 30 s (0.5, 1, 2, ... 30 by default), so a transient outage is not spent
    in milliseconds (#562). ``backoff=0`` reconnects at once. ``sleep`` is the wait function
    (``time.sleep``; tests pass their own). No wait follows the last failure.
    """
    if backoff < 0:
        raise ValueError("backoff must be zero or positive")
    resumable = _takes_start(connect)
    last: Any = None
    yielded = 0
    failures = 0
    while True:
        try:
            if resumable:
                items: Iterable[Any] = connect(last)
            else:
                items = itertools.islice(connect(), yielded, None)
            for item in items:
                if resumable:
                    if not (isinstance(item, tuple) and len(item) == 2):
                        raise _NotAnOffsetItem("a resumable connection must yield (offset, batch)")
                    last = item[0]
                yielded += 1
                failures = 0
                yield item
            return
        except retry_on as exc:
            if isinstance(exc, _NotAnOffsetItem):
                raise
            failures += 1
            if failures >= max_attempts:
                raise
            if backoff:
                sleep(min(backoff * 2.0 ** min(failures - 1, 64), _MAX_BACKOFF))
