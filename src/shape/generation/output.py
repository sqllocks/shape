"""Writing generated tables: every format goes through a ``shape.sinks`` plugin (P4-06).

``write_result`` writes a finished ``GenerationResult`` (every post-pass applied). ``write_engine``
streams a schema that needs no post-pass chunk by chunk, so chunk *n* is written while chunk
*n* + 1 is generated (T-17), and falls back to ``write_result`` otherwise. ``format_summary`` is
the ``summary`` output. Tables are written in parallel; each writer is closed before the call
returns (the files can then be replaced or deleted on Windows).
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.generation.engine import Engine, GenerationResult, worker_threads
from shape.generation.schema import GenSchema
from shape.plugins.host import default_host

FORMATS = ("summary", "csv", "tsv", "jsonl", "parquet", "excel", "sql", "delta")
EXTENSIONS = {
    "csv": "csv",
    "tsv": "tsv",
    "jsonl": "jsonl",
    "parquet": "parquet",
    "excel": "xlsx",
    "sql": "sql",
}
_QUEUE_DEPTH = 2
_POST_PASS_STRATEGIES = ("computed",)


def format_summary(result: GenerationResult) -> str:
    """The ``summary`` output: schema, seed, the table list with rows and columns, the total."""
    schema = result.schema
    lines = [
        "Shape Generation Result",
        "=" * 40,
        f"Schema: {schema.model.name}",
        f"Domain: {schema.model.domain}",
        f"Mode:   {schema.model.schema_mode}",
        f"Seed:   {schema.model.seed}",
        f"Time:   {result.elapsed_seconds:.1f}s",
        "",
        f"{'Table':<25} {'Rows':>12} {'Columns':>8}",
        "-" * 45,
    ]
    for name in result.generation_order:
        table = result.tables[name]
        lines.append(f"{name:<25} {table.num_rows:>12,} {table.num_columns:>8}")
    lines.append("-" * 45)
    lines.append(f"{'TOTAL':<25} {sum(result.row_counts.values()):>12,}")
    return "\n".join(lines)


def sql_options(schema: GenSchema, table: str) -> dict[str, Any]:
    """The per-table options the ``sql`` sink takes from the generation schema."""
    tdef = schema.tables[table]
    return {
        "primary_key": list(tdef.primary_key),
        "columns": {
            c.name: {
                "type": c.type,
                "nullable": c.nullable or c.null_rate > 0,
                "max_length": c.max_length,
                "precision": c.precision,
                "scale": c.scale,
            }
            for c in tdef.columns.values()
        },
    }


def _sink(fmt: str) -> Any:
    if fmt not in EXTENSIONS and fmt != "delta":
        raise ValueError(f"unknown format {fmt!r}; choose one of {', '.join(FORMATS)}")
    return default_host().get("shape.sinks", fmt)


class _LazySink:
    """The sink of ``fmt``, loaded by the first thread that writes to it. The format is checked
    now; importing the sink (Parquet alone is about 15 ms) happens on a writer thread, while the
    first tables are being generated, instead of before the first row."""

    def __init__(self, fmt: str) -> None:
        if fmt not in EXTENSIONS and fmt != "delta":
            raise ValueError(f"unknown format {fmt!r}; choose one of {', '.join(FORMATS)}")
        self._fmt = fmt
        self._sink: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        with self._lock:
            if self._sink is None:
                self._sink = _sink(self._fmt)
            return self._sink

    def preload(self) -> None:
        """Import the sink now (run on a thread started for it, while generation begins)."""
        self._load()

    def write(self, *args: Any, **kwargs: Any) -> Any:
        return self._load().write(*args, **kwargs)

    def open_native(self, target: Path, schema: pa.Schema, options: dict[str, Any]) -> Any:
        """The sink's native writer for ``target`` (``write_batch``, ``finish``, ``wait``), or
        ``None`` when the sink has none or it cannot write this file."""
        opener = getattr(self._load(), "open_native", None)
        return None if opener is None else opener(target, schema, options)


def _target(fmt: str, output_dir: Path, table: str) -> Path:
    # Delta writes <output_dir>/<table>/ itself; files are <table>.<extension>.
    from shape.security.names import contained

    return output_dir if fmt == "delta" else contained(output_dir, table, f".{EXTENSIONS[fmt]}")


def _options(fmt: str, schema: GenSchema, table: str, options: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(options)
    if fmt == "sql":
        merged = {**sql_options(schema, table), **merged}
        model = schema.model
        merged.setdefault(
            "header",
            [
                f"Domain: {model.domain} | Mode: {model.schema_mode} | Seed: {model.seed}",
            ],
        )
    return merged


def _paths(fmt: str, output_dir: Path, tables: list[str]) -> list[Path]:
    return [output_dir / t if fmt == "delta" else _target(fmt, output_dir, t) for t in tables]


def write_result(
    result: GenerationResult,
    fmt: str,
    output_dir: str | Path,
    *,
    max_workers: int | None = None,
    **options: Any,
) -> list[Path]:
    """Write every table of ``result`` as ``fmt``; return the files (or Delta directories)."""
    sink = _sink(fmt)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    names = list(result.generation_order)

    def one(name: str) -> None:
        table = result.tables[name]
        sink.write(
            str(_target(fmt, out, name)),
            name,
            iter(table.to_batches()),
            schema=table.schema,
            **_options(fmt, result.schema, name, options),
        )

    _run_parallel(names, one, _writers(max_workers))
    return _paths(fmt, out, names)


def _writers(max_workers: int | None) -> int:
    """Writer threads: ``max_workers``, else ``SHAPE_THREADS`` (every core when unset), up to 4."""
    return max_workers if max_workers is not None else min(4, worker_threads())


def _run_parallel(names: list[str], work: Callable[[str], None], max_workers: int) -> None:
    if max_workers <= 1 or len(names) <= 1:
        for n in names:
            work(n)
        return
    with ThreadPoolExecutor(max_workers=min(max_workers, len(names))) as pool:
        for future in [pool.submit(work, n) for n in names]:
            future.result()


def needs_post_pass(schema: GenSchema) -> bool:
    """True when a table can only be finished after it is whole (compute phase, business-rule
    repair, correlation), so it cannot be streamed chunk by chunk."""
    if schema.business_rules or any(schema.correlated_columns.values()):
        return True
    return any(
        c.strategy in _POST_PASS_STRATEGIES
        for t in schema.tables.values()
        for c in t.columns.values()
    )


def _prefetch(source: Iterator[pa.RecordBatch]) -> Iterator[pa.RecordBatch]:
    """Yield ``source``'s batches while a thread already produces the next ones."""
    buffer: queue.Queue[Any] = queue.Queue(maxsize=_QUEUE_DEPTH)
    done = object()
    stop = threading.Event()

    def produce() -> None:
        try:
            for batch in source:
                while not stop.is_set():
                    try:
                        buffer.put(batch, timeout=0.1)
                        break
                    except queue.Full:
                        continue
                if stop.is_set():
                    return
            buffer.put(done)
        except BaseException as exc:  # handed to the consumer
            buffer.put(exc)

    thread = threading.Thread(target=produce, daemon=True)
    thread.start()
    try:
        while True:
            item = buffer.get()
            if item is done:
                return
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        stop.set()
        thread.join()


def _write_overlapped(
    engine: Engine, fmt: str, output_dir: str | Path, max_workers: int, options: Mapping[str, Any]
) -> list[Path]:
    """Generate the whole schema and write each table as soon as it is final: a table that no
    post-pass changes is written chunk by chunk while it is generated (``Engine.generate``'s
    ``on_batch``), and the others after the post-passes (``on_table``), so the writes overlap
    the generation of the other tables and the post-passes.

    A table of a format with a native writer (Parquet with the native kernel) is written from the
    thread that delivers it: the writer takes the batches and encodes them on its own threads, so
    no table waits for another and no writer thread of ours is needed. Every other table goes to
    ``max_workers`` writer threads, each writing one table from a queue."""
    sink = _LazySink(fmt)
    threading.Thread(target=sink.preload, name="shape-sink", daemon=True).start()
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    queues: dict[str, queue.Queue[pa.RecordBatch | None]] = {}
    native: dict[str, Any] = {}  # table to its native writer
    pooled: set[str] = set()  # tables written by a writer thread
    futures: list[Future[None]] = []
    pools: list[ThreadPoolExecutor] = []

    def write(name: str, batches: Iterator[pa.RecordBatch], schema: pa.Schema | None) -> None:
        extra = {"schema": schema} if schema is not None else {}
        sink.write(
            str(_target(fmt, out, name)),
            name,
            batches,
            **extra,
            **_options(fmt, engine.schema, name, options),
        )

    def submit(name: str, batches: Iterator[pa.RecordBatch], schema: pa.Schema | None) -> None:
        if not pools:
            pools.append(ThreadPoolExecutor(max_workers=max(1, max_workers)))
        futures.append(pools[0].submit(write, name, batches, schema))

    def drain(pending: queue.Queue[pa.RecordBatch | None]) -> Iterator[pa.RecordBatch]:
        while (batch := pending.get()) is not None:
            yield batch

    def route(name: str, schema: pa.Schema) -> Any:
        """The native writer of ``name``, or ``None`` when a writer thread writes it."""
        if name in pooled:
            return None
        if name not in native:
            writer = sink.open_native(
                _target(fmt, out, name), schema, _options(fmt, engine.schema, name, options)
            )
            if writer is None:
                pooled.add(name)
                return None
            native[name] = writer
        return native[name]

    def on_batch(name: str, batch: pa.RecordBatch | None) -> None:
        if batch is not None:
            writer = route(name, batch.schema)
            if writer is not None:
                writer.write_batch(batch)
                return
        elif name in native:
            native[name].finish()
            return
        pending = queues.get(name)
        if pending is None:
            pending = queues[name] = queue.Queue()
            submit(name, drain(pending), None)
        pending.put(batch)

    def on_table(name: str, table: pa.Table) -> None:
        writer = route(name, table.schema)
        if writer is None:
            submit(name, iter(table.to_batches()), table.schema)
            return
        for batch in table.to_batches():
            writer.write_batch(batch)
        writer.finish()

    def settle(raise_errors: bool) -> None:
        """Wait for every native file and writer thread; the first error is raised."""
        first: BaseException | None = None
        for writer in native.values():
            try:
                writer.finish()
                writer.wait()
            except BaseException as exc:
                first = first or exc
        for pending in queues.values():  # let the writer threads finish what they have
            pending.put(None)
        for pool in pools:
            pool.shutdown(wait=True)
        for future in futures:
            failure = future.exception()
            if failure is not None:
                first = first or failure
        if first is not None and raise_errors:
            raise first

    try:
        result = engine.generate(on_table=on_table, on_batch=on_batch)
    except BaseException:
        settle(raise_errors=False)
        raise
    settle(raise_errors=True)
    return _paths(fmt, out, list(result.generation_order))


def write_engine(
    engine: Engine,
    fmt: str,
    output_dir: str | Path,
    *,
    chunk_rows: int | None = None,
    max_workers: int | None = None,
    **options: Any,
) -> list[Path]:
    """Generate and write every table. A schema with no post-pass is streamed (generation of the
    next chunk overlaps the write of this one); otherwise the whole result is written.

    One core is left to the writer threads: a Parquet file is encoded by one thread, and with every
    core busy generating, the encoder of the largest table is what the run ends up waiting for
    (``SHAPE_THREADS``, when set, is used as it is)."""
    engine.reserved_cores = max(engine.reserved_cores, 1)
    if needs_post_pass(engine.schema):
        return _write_overlapped(engine, fmt, output_dir, _writers(max_workers), options)
    engine.schema.validate_or_raise()
    sink = _LazySink(fmt)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    names = list(engine.order)

    def one(name: str) -> None:
        sink.write(
            str(_target(fmt, out, name)),
            name,
            _prefetch(engine.iter_chunks(name, chunk_rows)),
            **_options(fmt, engine.schema, name, options),
        )

    _run_parallel(names, one, _writers(max_workers))
    return _paths(fmt, out, names)
