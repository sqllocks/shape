"""Events into tables: any ``shape.sinks`` sink as the destination of a stream (``--to URI``).

``TableEventSink`` splits the events of each ``send`` by table and writes them to the sink that
registered the URI's scheme (``abfss://``, ``delta+abfss://``, ``mssql://``, ``postgresql://``,
``mysql://``), so a stream lands in files, Delta tables and database tables, and a reader sees the
rows while the stream runs:

* files (``abfss://``) roll a new file every ``roll_rows`` rows or ``roll_seconds`` seconds, and
  at every checkpoint, each file written under a temporary name and renamed when complete;
* Delta commits at every checkpoint (and every ``commit_rows`` / ``commit_seconds``);
* databases commit every ``commit_rows`` rows (every batch when it is not given).

A table gets the event's columns less ``_shape_table`` (it is the table), so ``_shape_seq`` (the
idempotency key with the table) and ``_shape_event_time`` are columns: delivery is at least once
and a consumer removes repeats on ``_shape_seq``. A sink with ``open_table`` streams natively; any
other sink's ``write`` is driven on a thread that consumes batches as they arrive.

``flush`` (called before each checkpoint) makes everything sent so far visible and durable in the
sink, so the checkpoint never claims more than the destination holds.
"""

from __future__ import annotations

import queue
import threading
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.errors import ShapeError
from shape.streaming.emit.formats import FIELD_POISON, FIELD_TABLE

SYNTHETIC_METADATA = {b"shape_synthetic": b"true"}


class _ThreadedWriter:
    """A sink's ``write`` run on a thread over a queue of batches (the sink consumes the
    iterator incrementally)."""

    def __init__(self, sink: Any, uri: str, table: str, schema: pa.Schema, options: dict[str, Any]):
        self._q: queue.Queue[pa.RecordBatch | None] = queue.Queue(maxsize=4)
        self._cond = threading.Condition()
        self._put = self._taken = self._done = 0
        self._error: BaseException | None = None
        self._ended = False  # the end marker was taken: nothing more will arrive
        self.rows = 0

        def run() -> None:
            try:
                self.rows = int(sink.write(uri, table, self._batches(), schema=schema, **options))
            except BaseException as exc:
                with self._cond:
                    self._error = exc
                    self._cond.notify_all()
                while not self._ended and self._q.get() is not None:
                    pass  # keep the producer from blocking on us

        self._thread = threading.Thread(target=run, name=f"shape-table-{table}", daemon=True)
        self._thread.start()

    def _batches(self) -> Any:
        while True:
            with self._cond:
                self._done = self._taken
                self._cond.notify_all()
            item = self._q.get()
            if item is None:
                self._ended = True
                return
            with self._cond:
                self._taken += 1
            yield item

    def _check(self) -> None:
        if self._error is not None:
            raise self._error

    def write_batch(self, batch: pa.RecordBatch) -> None:
        self._check()
        self._q.put(batch)
        with self._cond:
            self._put += 1
        self._check()

    def flush(self) -> None:
        with self._cond:
            self._cond.wait_for(lambda: self._error is not None or self._done >= self._put)
        self._check()

    def close(self, **_: Any) -> int:
        self._q.put(None)
        self._thread.join()
        self._check()
        return self.rows

    def abort(self) -> None:
        self._q.put(None)
        self._thread.join(timeout=5)


class TableEventSink:
    """Stream events into the sink of ``uri`` (see the module docstring)."""

    def __init__(
        self,
        uri: str,
        *,
        synthetic: bool = True,
        resuming: bool = False,
        options: Any = None,
    ) -> None:
        from shape.io.targets import sink_for_target

        self.uri = uri
        self.synthetic = synthetic
        self.resuming = resuming
        self._name, self._sink = sink_for_target(uri)
        self._options = options
        self._writers: dict[str, Any] = {}
        self.rows: dict[str, int] = {}

    def _table_options(self, table: str) -> dict[str, Any]:
        opts: dict[str, Any] = {}
        if self._options is not None:
            opts = self._options.for_sink(self._name, None, table)
        files = self._name in ("abfss", "delta")
        if self._name == "abfss":
            opts.setdefault("streaming", True)  # every checkpoint completes a numbered file
        if self.resuming:
            opts.setdefault("mode" if files else "write_mode", "append")
        if not files:
            opts.setdefault("commit_rows", 1)  # every batch: readers see rows while it runs
        return opts

    def _writer(self, table: str, schema: pa.Schema) -> Any:
        writer = self._writers.get(table)
        if writer is not None:
            return writer
        if self.synthetic:
            schema = schema.with_metadata(SYNTHETIC_METADATA)
        opts = self._table_options(table)
        opener = getattr(self._sink, "open_table", None)
        if callable(opener):
            writer = opener(self.uri, table, schema, **opts)
        else:
            writer = _ThreadedWriter(self._sink, self.uri, table, schema, opts)
        self._writers[table] = writer
        return writer

    def send(self, batch: pa.RecordBatch) -> None:
        if FIELD_TABLE not in batch.schema.names:
            raise ShapeError("a stream event has no _shape_table column")
        tables = batch.column(FIELD_TABLE)
        names = pc.unique(tables).to_pylist()
        keep = [n for n in batch.schema.names if n not in (FIELD_TABLE, FIELD_POISON)]
        for table in names:
            part = batch if len(names) == 1 else batch.filter(pc.equal(tables, table))
            part = part.select(keep)
            if self.synthetic:
                part = part.replace_schema_metadata(SYNTHETIC_METADATA)
            self._writer(table, part.schema).write_batch(part)
            self.rows[table] = self.rows.get(table, 0) + part.num_rows

    def flush(self) -> None:
        first: BaseException | None = None
        for writer in self._writers.values():
            try:
                writer.flush()
            except BaseException as exc:
                first = first or exc
        if first is not None:
            raise first

    def close(self) -> None:
        first: BaseException | None = None
        for writer in self._writers.values():
            try:
                writer.close()
            except BaseException as exc:
                first = first or exc
                abort = getattr(writer, "abort", None)
                if callable(abort):
                    abort()
        if first is not None:
            raise first
