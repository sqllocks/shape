"""``ParquetSink``: each table as part files, ``<dir>/<table>/part-NNNNNN.parquet``.

Part *i* of a table holds rows ``i * chunk_rows`` up to ``(i + 1) * chunk_rows``, whatever the size
of the batches it receives, so the files do not depend on how the engine cut its chunks. Files are
encoded by Shape's own Parquet sink (snappy, dictionary encoding, T-17) on a few writer threads and
renamed into place only when complete, so a part that exists is a whole part.

``resume=True`` skips a part that already exists with the right number of rows (a run that was
stopped and is run again). A table that is finished gets ``_COMPLETE`` (its row and part count).
"""

from __future__ import annotations

import json
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shape.scale.sinks.base import BaseSink

if TYPE_CHECKING:
    import pyarrow as pa  # type: ignore[import-untyped]

    from shape.generation.schema import GenSchema

PART_NAME = "part-{:06d}.parquet"
COMPLETE = "_COMPLETE"


def part_rows_ok(path: Path, rows: int) -> bool:
    """True when ``path`` is a readable Parquet file of exactly ``rows`` rows."""
    try:
        import pyarrow.parquet as pq  # type: ignore[import-untyped]

        return bool(pq.ParquetFile(path).metadata.num_rows == rows)
    except Exception:
        return False


class ParquetSink(BaseSink):
    """Part files per table under ``output_dir``."""

    name = "parquet"

    def __init__(
        self,
        output_dir: str | Path,
        chunk_rows: int = 500_000,
        resume: bool = False,
        writer_threads: int | None = None,
    ) -> None:
        if chunk_rows < 1:
            raise ValueError("chunk_rows must be at least 1")
        self._base = Path(output_dir)
        self._chunk_rows = chunk_rows
        self._resume = resume
        self._threads = writer_threads
        self._pool: ThreadPoolExecutor | None = None
        self._pending: dict[str, list[pa.RecordBatch]] = {}
        self._pending_rows: dict[str, int] = {}
        self._parts: dict[str, int] = {}
        self._rows: dict[str, int] = {}
        self._futures: list[Future[bool]] = []
        self._empty: dict[str, pa.RecordBatch] = {}
        self._lock = threading.Lock()
        self._sink: Any = None
        self._aborted = False
        self.parts_written = 0
        self.parts_skipped = 0

    @property
    def output_dir(self) -> Path:
        return self._base

    def open(self, schema: GenSchema | None) -> None:
        from shape.generation.engine import worker_threads
        from shape.plugins.host import default_host

        self._base.mkdir(parents=True, exist_ok=True)
        self._sink = default_host().get("shape.sinks", "parquet")
        self._pending, self._pending_rows, self._parts, self._rows = {}, {}, {}, {}
        self._empty = {}
        self._futures = []
        self._aborted = False
        self.parts_written = self.parts_skipped = 0
        n = self._threads if self._threads is not None else min(4, worker_threads())
        self._pool = ThreadPoolExecutor(max_workers=max(1, n), thread_name_prefix="shape-part")

    def write_batch(self, table: str, batch: pa.RecordBatch) -> None:
        import pyarrow as pa

        self._parts.setdefault(table, 0)
        self._rows.setdefault(table, 0)
        (self._base / table).mkdir(parents=True, exist_ok=True)
        pending = self._pending.setdefault(table, [])
        if batch.num_rows == 0:  # an empty table still gets one (empty) part, to carry its schema
            self._empty.setdefault(table, batch)
            return
        pending.append(batch)
        self._pending_rows[table] = self._pending_rows.get(table, 0) + batch.num_rows
        while self._pending_rows[table] >= self._chunk_rows:
            whole = pa.Table.from_batches(self._pending[table])
            head, rest = whole.slice(0, self._chunk_rows), whole.slice(self._chunk_rows)
            self._submit(table, head.combine_chunks().to_batches())
            self._pending[table] = rest.combine_chunks().to_batches() if rest.num_rows else []
            self._pending_rows[table] = rest.num_rows
        self._raise_done()

    def finish_table(self, table: str) -> None:
        if table not in self._parts:
            return
        rows_left = self._pending_rows.get(table, 0)
        if rows_left:
            self._submit(table, self._pending[table])
            self._pending[table], self._pending_rows[table] = [], 0
        elif self._parts[table] == 0 and table in self._empty:
            self._submit(table, [self._empty[table]])
        self._drain()
        target = self._base / table
        target.mkdir(parents=True, exist_ok=True)
        self.mark_complete(table, self._rows[table], self._parts[table])

    def mark_complete(self, table: str, rows: int, parts: int) -> None:
        """Write ``table``'s ``_COMPLETE`` marker (workers made its part files)."""
        target = self._base / table
        target.mkdir(parents=True, exist_ok=True)
        tmp = target / (COMPLETE + ".tmp")
        tmp.write_text(json.dumps({"rows": rows, "parts": parts}), encoding="utf-8")
        os.replace(tmp, target / COMPLETE)

    def abort(self) -> None:
        """The run stopped early: rows not yet in a full part are dropped and no table gets its
        marker, so a table that was cut short does not look finished. Part files already written
        stay (a resume skips them)."""
        self._aborted = True

    def close(self) -> None:
        try:
            if self._aborted:
                self._drain_quietly()
            else:
                for table in list(self._parts):
                    if self._pending_rows.get(table) or (
                        self._parts[table] == 0 and table in self._empty
                    ):
                        self.finish_table(table)
                self._drain()
        finally:
            pool, self._pool = self._pool, None
            if pool is not None:
                pool.shutdown(wait=True)

    # ---- part files ---------------------------------------------------------------------

    def _submit(self, table: str, batches: list[pa.RecordBatch]) -> None:
        index = self._parts[table]
        rows = sum(b.num_rows for b in batches)
        self._parts[table] = index + 1
        self._rows[table] += rows
        if self._pool is None:
            raise RuntimeError("ParquetSink is not open")
        self._futures.append(self._pool.submit(self._write_part, table, index, batches, rows))

    def _write_part(self, table: str, index: int, batches: list[pa.RecordBatch], rows: int) -> bool:
        path = self._base / table / PART_NAME.format(index)
        if self._resume and part_rows_ok(path, rows):
            with self._lock:
                self.parts_skipped += 1
            return False
        tmp = path.with_name(path.name + ".tmp")
        self._sink.write(str(tmp), table, iter(batches), schema=batches[0].schema)
        os.replace(tmp, path)
        with self._lock:
            self.parts_written += 1
        return True

    def _raise_done(self) -> None:
        """Surface the error of a part that has failed, without waiting for the others."""
        still = []
        for future in self._futures:
            if future.done():
                future.result()
            else:
                still.append(future)
        self._futures = still

    def _drain(self) -> None:
        futures, self._futures = self._futures, []
        for future in futures:
            future.result()

    def _drain_quietly(self) -> None:
        """Wait for the parts in flight; a part that failed is left for a resume to write."""
        futures, self._futures = self._futures, []
        for future in futures:
            future.exception()
