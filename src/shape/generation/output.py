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
from concurrent.futures import ThreadPoolExecutor
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


def _target(fmt: str, output_dir: Path, table: str) -> Path:
    # Delta writes <output_dir>/<table>/ itself; files are <table>.<extension>.
    return output_dir if fmt == "delta" else output_dir / f"{table}.{EXTENSIONS[fmt]}"


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
    the generation of the other tables and the post-passes."""
    sink = _sink(fmt)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    queues: dict[str, queue.Queue[pa.RecordBatch | None]] = {}

    def write(name: str, batches: Iterator[pa.RecordBatch], schema: pa.Schema | None) -> None:
        extra = {"schema": schema} if schema is not None else {}
        sink.write(
            str(_target(fmt, out, name)),
            name,
            batches,
            **extra,
            **_options(fmt, engine.schema, name, options),
        )

    def drain(pending: queue.Queue[pa.RecordBatch | None]) -> Iterator[pa.RecordBatch]:
        while (batch := pending.get()) is not None:
            yield batch

    futures = []
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:

        def on_batch(name: str, batch: pa.RecordBatch | None) -> None:
            pending = queues.get(name)
            if pending is None:
                pending = queues[name] = queue.Queue()
                futures.append(pool.submit(write, name, drain(pending), None))
            pending.put(batch)

        def on_table(name: str, table: pa.Table) -> None:
            futures.append(pool.submit(write, name, iter(table.to_batches()), table.schema))

        try:
            result = engine.generate(on_table=on_table, on_batch=on_batch)
        except BaseException:
            for pending in queues.values():  # let the writers finish what they have
                pending.put(None)
            raise
    for future in futures:
        future.result()
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
    next chunk overlaps the write of this one); otherwise the whole result is written."""
    if needs_post_pass(engine.schema):
        return _write_overlapped(engine, fmt, output_dir, _writers(max_workers), options)
    engine.schema.validate_or_raise()
    sink = _sink(fmt)
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
