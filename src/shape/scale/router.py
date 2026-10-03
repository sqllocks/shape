"""``ScaleRouter``: one generation run, fanned out to sinks, in one of the local modes (P6-13).

* ``local_single``: generation on one thread. The reference for what a run produces.
* ``local_mp``: generation on every core (the engine's worker threads; ``max_workers`` or
  ``SHAPE_THREADS`` limits them). The output is the same as ``local_single``'s: the engine's
  result does not depend on how many threads made it.

``processes=N`` (with the Parquet sink alone, and a schema with no post-pass) makes the chunk files
in N worker processes instead, each writing its own part files (:mod:`shape.scale.chunk_worker`).
That is the one case where processes do something threads do not: the part files of a very large
table are encoded in parallel, off the GIL, and a stopped run resumes at a chunk.

Row counts are exact in every mode: a table has the rows of the scale preset (or override), and
foreign keys point at keys that exist in the full parent table, whichever chunk they are in.
"""

from __future__ import annotations

import contextlib
import logging
import os
import queue
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from shape.generation.engine import THREADS_ENV, Engine
from shape.generation.output import needs_post_pass
from shape.scale.sink_registry import SinkRegistry
from shape.scale.sinks.base import Sink, SinkError

if TYPE_CHECKING:
    import pyarrow as pa  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

LOCAL_MODES = ("local_single", "local_mp")
SCALE_MODES = (*LOCAL_MODES, "fabric_spark")
DEFAULT_CHUNK_SIZE = 500_000
_QUEUE_DEPTH = 4


class ScaleCancelled(Exception):
    """The run was cancelled between two chunks; what was written stays on disk."""


@dataclass
class ScaleStats:
    """What a run did."""

    mode: str
    rows_generated: int
    tables: dict[str, int]
    chunks: int
    elapsed_seconds: float
    throughput_rows_per_sec: int
    peak_rss_gb: float
    threads: int
    processes: int = 0
    parts_skipped: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "rows_generated": self.rows_generated,
            "tables": dict(self.tables),
            "chunks": self.chunks,
            "elapsed_seconds": self.elapsed_seconds,
            "throughput_rows_per_sec": self.throughput_rows_per_sec,
            "peak_rss_gb": self.peak_rss_gb,
            "threads": self.threads,
            "processes": self.processes,
            "parts_skipped": self.parts_skipped,
            **self.extra,
        }


def _windows_peak_working_set_bytes() -> int:
    """Peak working set of this process from ``GetProcessMemoryInfo`` (Windows only)."""
    if sys.platform != "win32":
        raise OSError("GetProcessMemoryInfo exists only on Windows")
    import ctypes
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(
        kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    ):
        raise OSError("GetProcessMemoryInfo failed")
    return int(counters.PeakWorkingSetSize)


def peak_rss_gb() -> float:
    """The process's peak resident memory in GB (0.0 where the platform cannot say).

    POSIX reads ``ru_maxrss`` (kilobytes on Linux, bytes on macOS); Windows has no
    ``resource`` module and reads the peak working set from ``GetProcessMemoryInfo``.
    """
    try:
        if sys.platform == "win32":
            return round(_windows_peak_working_set_bytes() / 1024**3, 3)
        import resource

        peak = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return round(peak / (1024**3 if sys.platform == "darwin" else 1024**2), 3)
    except (ImportError, OSError, AttributeError):
        return 0.0


@contextlib.contextmanager
def thread_limit(threads: int | None) -> Iterator[None]:
    """``SHAPE_THREADS`` set to ``threads`` for the block (the engine reads it per run)."""
    if threads is None:
        yield
        return
    previous = os.environ.get(THREADS_ENV)
    os.environ[THREADS_ENV] = str(threads)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(THREADS_ENV, None)
        else:
            os.environ[THREADS_ENV] = previous


def _threads_for(mode: str, max_workers: int | None) -> int | None:
    if mode == "local_single":
        return 1
    if max_workers is not None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        return max_workers
    return None  # the engine's default: SHAPE_THREADS, else every core


class ScaleRouter:
    """Runs ``engine`` into ``sinks``.

    ``on_progress(info)`` is called after every chunk with ``rows_done``, ``rows_total``,
    ``chunks`` and ``table``. ``cancel`` is a ``threading.Event``: set, the run stops before the
    next chunk and ``run`` raises :class:`ScaleCancelled`.
    """

    def __init__(
        self,
        engine: Engine,
        sinks: Sequence[Sink],
        *,
        mode: str = "local_mp",
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        max_workers: int | None = None,
        processes: int = 0,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
        cancel: threading.Event | None = None,
        resume: bool = False,
    ) -> None:
        if mode not in LOCAL_MODES:
            raise ValueError(
                f"unknown local mode {mode!r}; the local modes are: {', '.join(LOCAL_MODES)}"
            )
        if chunk_size < 1:
            raise ValueError("chunk_size must be at least 1")
        if processes < 0:
            raise ValueError("processes must not be negative")
        if processes and mode == "local_single":
            raise ValueError("processes needs local_mp: local_single is one thread")
        self.engine = engine
        self.sinks = list(sinks)
        self.mode = mode
        self.chunk_size = chunk_size
        self.max_workers = max_workers
        self.processes = processes
        self._on_progress = on_progress
        self._cancel = cancel or threading.Event()
        self._resume = resume
        self._registry = SinkRegistry(self.sinks)
        self._rows_done = 0
        self._chunks = 0
        self._tables: dict[str, int] = {}
        self._rows_total = sum(engine.row_counts[n] for n in engine.order if n in engine.row_counts)

    # ---- the run ------------------------------------------------------------------------

    def run(self) -> ScaleStats:
        started = time.perf_counter()
        self._rows_done = self._chunks = 0
        self._tables = {}
        threads = _threads_for(self.mode, self.max_workers)
        used_processes = 0
        skipped = 0
        self._registry.open(self.engine.schema)
        try:
            if self.processes:
                used_processes, skipped = self._run_processes()
            if not used_processes:
                with thread_limit(threads):
                    self._run_threads()
        finally:
            self._registry.close()
        elapsed = time.perf_counter() - started
        skipped += sum(int(getattr(s, "parts_skipped", 0)) for s in self.sinks)
        rows = sum(self._tables.values())
        from shape.generation.engine import worker_threads

        return ScaleStats(
            mode=self.mode,
            rows_generated=rows,
            tables=dict(self._tables),
            chunks=self._chunks,
            elapsed_seconds=round(elapsed, 3),
            throughput_rows_per_sec=int(rows / max(elapsed, 0.001)),
            peak_rss_gb=peak_rss_gb(),
            threads=0
            if used_processes
            else (1 if self.mode == "local_single" else (threads or worker_threads())),
            processes=used_processes,
            parts_skipped=skipped,
        )

    # ---- threads ------------------------------------------------------------------------

    def _run_threads(self) -> None:
        engine = self.engine
        pending: queue.Queue[tuple[str, str, Any] | None] = queue.Queue(maxsize=_QUEUE_DEPTH)
        failure: list[BaseException] = []

        def dispatch() -> None:
            while (item := pending.get()) is not None:
                kind, table, payload = item
                if failure:
                    continue  # keep draining so the producer never blocks on a full queue
                try:
                    if kind == "batch":
                        self._registry.write_batch(table, payload)
                    else:
                        self._registry.finish_table(table)
                except BaseException as exc:
                    failure.append(exc)

        def put(kind: str, table: str, payload: Any) -> None:
            if self._cancel.is_set():
                raise ScaleCancelled("the run was cancelled")
            if failure:
                raise failure[0]
            pending.put((kind, table, payload))

        def count(table: str, batch: pa.RecordBatch) -> None:
            self._tables[table] = self._tables.get(table, 0) + batch.num_rows
            self._rows_done += batch.num_rows
            self._chunks += 1
            self._progress(table)

        def on_batch(table: str, batch: pa.RecordBatch | None) -> None:
            if batch is None:
                put("finish", table, None)
                return
            count(table, batch)
            put("batch", table, batch)

        def on_table(table: str, whole: pa.Table) -> None:
            if not whole.num_rows:
                self._tables.setdefault(table, 0)
            for batch in whole.to_batches(max_chunksize=self.chunk_size):
                count(table, batch)
                put("batch", table, batch)
            if not whole.num_rows:
                put("batch", table, _empty_batch(whole))
            put("finish", table, None)

        worker = threading.Thread(target=dispatch, name="shape-scale-dispatch", daemon=True)
        worker.start()
        try:
            engine.generate(on_table=on_table, on_batch=on_batch)
        finally:
            pending.put(None)
            worker.join()
        if failure:
            exc = failure[0]
            raise exc

    # ---- processes ----------------------------------------------------------------------

    def _run_processes(self) -> tuple[int, int]:
        """Part files from worker processes; returns ``(processes used, parts skipped)``, or
        ``(0, 0)`` when the run does not qualify and the threads do it."""
        from shape.scale.chunk_worker import generate_chunk_file
        from shape.scale.sinks.parquet import ParquetSink

        engine = self.engine
        only = self.sinks[0] if len(self.sinks) == 1 else None
        if not isinstance(only, ParquetSink):
            raise ValueError("processes needs the parquet sink alone (workers write part files)")
        if needs_post_pass(engine.schema):
            logger.warning(
                "processes: this schema has a post-pass (computed columns, rules or correlation), "
                "which needs whole tables; generating on threads instead"
            )
            return 0, 0
        if only._chunk_rows != self.chunk_size:
            raise ValueError("the parquet sink's chunk_rows must equal the router's chunk_size")
        spec = {
            "key": uuid.uuid4().hex,
            "schema": engine.schema.to_dict(),
            "seed": engine.seed,
            "row_counts": {n: int(engine.row_counts[n]) for n in engine.order},
            "chunk_rows": self.chunk_size,
        }
        jobs: list[tuple[str, int, int, int]] = []
        parts_of: dict[str, int] = {}
        for table in engine.order:
            total = int(engine.row_counts[table])
            starts = range(0, total, self.chunk_size) if total else [0]
            for i, start in enumerate(starts):
                jobs.append((table, i, start, min(self.chunk_size, total - start)))
            parts_of[table] = len(starts)
        done_parts: dict[str, int] = {}
        skipped = 0
        import multiprocessing

        pool = ProcessPoolExecutor(
            max_workers=min(self.processes, len(jobs)),
            mp_context=multiprocessing.get_context("spawn"),
        )
        try:
            futures = [
                pool.submit(
                    generate_chunk_file, spec, t, i, start, rows, str(only.output_dir), self._resume
                )
                for t, i, start, rows in jobs
            ]
            for future in as_completed(futures):
                if self._cancel.is_set():
                    raise ScaleCancelled("the run was cancelled")
                table, _, rows, was_skipped = future.result()
                skipped += int(was_skipped)
                self._tables[table] = self._tables.get(table, 0) + rows
                self._rows_done += rows
                self._chunks += 1
                done_parts[table] = done_parts.get(table, 0) + 1
                if done_parts[table] == parts_of[table]:
                    only.mark_complete(table, self._tables[table], parts_of[table])
                self._progress(table)
        except BaseException:
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            pool.shutdown(wait=True)
        return min(self.processes, len(jobs)), skipped

    def _progress(self, table: str) -> None:
        if self._on_progress is not None:
            self._on_progress(
                {
                    "table": table,
                    "rows_done": self._rows_done,
                    "rows_total": self._rows_total,
                    "chunks": self._chunks,
                }
            )


def _empty_batch(table: pa.Table) -> pa.RecordBatch:
    import pyarrow as pa

    return pa.RecordBatch.from_arrays(
        [pa.array([], type=f.type) for f in table.schema], schema=table.schema
    )


__all__ = [
    "DEFAULT_CHUNK_SIZE",
    "LOCAL_MODES",
    "SCALE_MODES",
    "ScaleCancelled",
    "ScaleRouter",
    "ScaleStats",
    "SinkError",
    "peak_rss_gb",
    "thread_limit",
]
