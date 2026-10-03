"""Consume a stream source with offset-committed checkpoints (P3-03).

``StreamConsumer`` reads ``(offset, batch)`` pairs from a ``StreamSource`` (the plugin protocol:
``read(uri, start)``), feeds each new batch to a windowed profiler, and every
``checkpoint_every`` batches commits a checkpoint: the source offset reached, the deduplication
positions and the profiler's window state, in one atomic file. A restarted consumer resumes
from the checkpoint; a dropped connection reconnects from the last processed offset.

Delivery is at least once and the state is exact: a source that reconnects or restarts may
deliver batches again, and those are dropped by offset before they reach the profiler. Windows
are yielded before the checkpoint that covers them is committed, so a crash between the two
yields a window again after the restart: windows are identified by ``(kind, start, end)``, and
a sink that stores them by that key sees each one once. Shape does not claim more than the
source gives: the guarantee here is about the profile state, not about the sink.

Deduplication on offset needs positions the consumer can compare:

* ``offset_column`` names an integer column with each row's offset in its partition
  (``partition_column`` names the partition, one partition when absent): rows at or past the
  partition's next expected offset are new, so replays that cut batches differently are fine;
* without it, a source offset whose values are all integers is read as
  ``{partition: next offset}``, and a batch is new when it advances some partition;
* any other offset is opaque: batches are processed as delivered and only resumed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from typing import Any, Protocol

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from .checkpoint import CheckpointError, FileCheckpointStore
from .runtime import WindowedProfiler, WindowProfile, restore_profiler

CHECKPOINT_FORMAT = "shape-stream-checkpoint-v1"
_IDENTITY = ("kind", "schema", "event_time", "allowed_lateness_us", "top_n", "config")
_COUNTERS = (
    "batches_read",
    "batches_processed",
    "duplicate_batches",
    "duplicate_rows",
    "reconnects",
    "checkpoints",
)


class _Offset(Protocol):
    @property
    def value(self) -> Mapping[str, Any]: ...


class _Reader(Protocol):
    def read(self, uri: str, start: Any = None, **options: Any) -> Iterator[tuple[Any, Any]]: ...


def _offset(value: Mapping[str, Any]) -> Any:
    from shape.plugins.api.v1 import StreamOffset

    return StreamOffset(dict(value))


class StreamConsumer:
    """Read a stream source into a windowed profiler, with checkpoints and reconnects.

    ``run()`` yields each closed window. With a ``store`` (a ``FileCheckpointStore``) the
    consumer starts from its checkpoint when there is one.
    """

    def __init__(
        self,
        source: _Reader,
        uri: str,
        profiler: WindowedProfiler,
        store: FileCheckpointStore | None = None,
        *,
        checkpoint_every: int = 10,
        offset_column: str | None = None,
        partition_column: str | None = None,
        max_attempts: int = 5,
        retry_on: tuple[type[BaseException], ...] = (ConnectionError, TimeoutError),
        on_reconnect: Callable[[int, BaseException], None] | None = None,
        options: Mapping[str, Any] | None = None,
    ) -> None:
        if checkpoint_every < 1 or max_attempts < 1:
            raise ValueError("checkpoint_every and max_attempts must be positive")
        if partition_column is not None and offset_column is None:
            raise ValueError("partition_column needs offset_column")
        self.source = source
        self.uri = uri
        self.profiler = profiler
        self.store = store
        self.checkpoint_every = checkpoint_every
        self.offset_column = offset_column
        self.partition_column = partition_column
        self.max_attempts = max_attempts
        self.retry_on = retry_on
        self.on_reconnect = on_reconnect
        self.options = dict(options or {})
        self.source_offset: dict[str, Any] | None = None  # position after the last new batch
        self.positions: dict[str, int] = {}  # partition -> next expected offset
        self.batches_read = 0
        self.batches_processed = 0
        self.duplicate_batches = 0
        self.duplicate_rows = 0
        self.reconnects = 0
        self.checkpoints = 0
        self._since_commit = 0
        if store is not None:
            self._resume(store.load_document())

    # ----------------------------------------------------------- checkpoints
    def _resume(self, doc: dict[str, Any] | None) -> None:
        if doc is None:
            return
        if doc.get("format") != CHECKPOINT_FORMAT:
            raise CheckpointError("not a stream consumer checkpoint")
        if doc["uri"] != self.uri:
            raise CheckpointError(
                f"the checkpoint is for {doc['uri']!r}, not {self.uri!r}; use another file"
            )
        mine = self.profiler.snapshot()
        theirs = doc["profiler"]
        if any(mine[k] != theirs.get(k) for k in _IDENTITY):
            raise CheckpointError("the checkpoint was taken with a different profiler")
        self.profiler = restore_profiler(theirs, late_sink=self.profiler.late_sink)
        self.source_offset = doc["offset"]
        self.positions = {str(k): int(v) for k, v in doc["positions"].items()}
        for name, value in doc["counters"].items():
            if name not in _COUNTERS:  # never an arbitrary attribute from a file
                raise CheckpointError(f"the checkpoint holds an unknown counter {name!r}")
            setattr(self, name, int(value))

    def commit(self) -> None:
        """Write a checkpoint now (a no-op without a store)."""
        self._since_commit = 0
        if self.store is None:
            return
        self.checkpoints += 1
        self.store.save_document(
            {
                "format": CHECKPOINT_FORMAT,
                "uri": self.uri,
                "offset": self.source_offset,
                "positions": self.positions,
                "counters": {name: getattr(self, name) for name in _COUNTERS},
                "profiler": self.profiler.snapshot(),
            }
        )

    # ----------------------------------------------------------- deduplicate
    def _fresh(
        self, offset: Any, batch: pa.RecordBatch
    ) -> tuple[pa.RecordBatch | None, dict[str, int]]:
        """The part of ``batch`` that is new (``None`` when all of it is a replay) and the
        positions to record once it has been processed."""
        if self.offset_column is not None:
            return self._fresh_rows(batch)
        value = getattr(offset, "value", None)
        if (
            isinstance(value, Mapping)
            and value
            and all(isinstance(v, int) and not isinstance(v, bool) for v in value.values())
        ):
            advanced = {str(p): int(v) for p, v in value.items()}
            if all(v <= self.positions.get(p, -1) for p, v in advanced.items()):
                self.duplicate_batches += 1
                self.duplicate_rows += batch.num_rows
                return None, {}
            return batch, advanced
        return batch, {}

    def _fresh_rows(self, batch: pa.RecordBatch) -> tuple[pa.RecordBatch | None, dict[str, int]]:
        assert self.offset_column is not None
        offsets = batch.column(self.offset_column)
        if not pa.types.is_integer(offsets.type):
            raise TypeError(f"offset column {self.offset_column!r} must be an integer column")
        off = offsets.cast(pa.int64()).fill_null(-1).to_numpy(zero_copy_only=False)
        if self.partition_column is None:
            parts = pa.array(["0"] * batch.num_rows)
        else:
            parts = batch.column(self.partition_column).cast(pa.string())
        names = pc.unique(parts)
        index = pc.index_in(parts, value_set=names).to_numpy(zero_copy_only=False)
        listed = [str(p) for p in names.to_pylist()]
        floor = np.array([self.positions.get(p, -(2**62)) for p in listed], dtype=np.int64)
        fresh = (off >= floor[index]) | (off < 0)  # a null offset cannot be checked: new
        n_new = int(np.count_nonzero(fresh))
        if n_new == 0:
            self.duplicate_batches += 1
            self.duplicate_rows += batch.num_rows
            return None, {}
        reached: dict[str, int] = {}
        for i, p in enumerate(listed):
            rows = (index == i) & fresh & (off >= 0)
            if rows.any():
                reached[p] = int(off[rows].max()) + 1
        if n_new < batch.num_rows:
            self.duplicate_rows += batch.num_rows - n_new
            return batch.filter(pa.array(fresh)), reached
        return batch, reached

    # ------------------------------------------------------------------- run
    def run(self) -> Iterator[WindowProfile]:
        """Consume until the source is exhausted, yielding each window as it closes."""
        if self.profiler.finished:
            return
        stalled = 0
        while True:
            start = None if self.source_offset is None else _offset(self.source_offset)
            try:
                for offset, batch in self.source.read(self.uri, start, **self.options):
                    self.batches_read += 1
                    fresh, reached = self._fresh(offset, batch)
                    if fresh is None:
                        continue
                    stalled = 0
                    closed = self.profiler.process(fresh)
                    for p, v in reached.items():
                        self.positions[p] = max(self.positions.get(p, -1), v)
                    value = getattr(offset, "value", None)
                    self.source_offset = None if value is None else dict(value)
                    self.batches_processed += 1
                    self._since_commit += 1
                    yield from closed
                    if self._since_commit >= self.checkpoint_every:
                        self.commit()
                break
            except self.retry_on as exc:
                stalled += 1
                self.reconnects += 1
                if stalled >= self.max_attempts:
                    raise
                if self.on_reconnect is not None:
                    self.on_reconnect(stalled, exc)
        yield from self.profiler.finish()
        self.commit()
