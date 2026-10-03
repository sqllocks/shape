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
from shape.plugins.schemes import local_path, require_scheme, uri_scheme

# The formats Shape ships, for help text. What is accepted is whatever ``shape.sinks`` plugins are
# installed (``available_formats()``), so a third-party sink needs no change here.
FORMATS = ("summary", "csv", "tsv", "jsonl", "parquet", "ipc", "excel", "sql", "delta")
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


def available_formats() -> tuple[str, ...]:
    """``summary`` and the name of every installed ``shape.sinks`` plugin, in a stable order:
    the built-ins first, then the others by name."""
    names = default_host().names("shape.sinks")
    ordered = [f for f in FORMATS if f in names]
    return ("summary", *ordered, *sorted(n for n in names if n not in FORMATS))


def format_argument(text: str) -> str:
    """The ``--format`` argument type: the name, if a sink is installed for it (or ``summary``)."""
    if text != "summary":
        _check_format(text)
    return text


def _check_format(fmt: str) -> None:
    if fmt == "summary" or fmt not in default_host().names("shape.sinks"):
        raise ValueError(f"unknown format {fmt!r}; choose one of {', '.join(available_formats())}")


def _sink(fmt: str) -> Any:
    _check_format(fmt)
    return default_host().get("shape.sinks", fmt)


def _extension(fmt: str, sink: Any) -> str:
    """The file extension of ``fmt``'s sink: its ``extension`` (the format name when it has none);
    an empty one marks a sink that writes a directory per table (Delta)."""
    value = getattr(sink, "extension", fmt)
    return value if isinstance(value, str) else fmt


class _LazySink:
    """The sink of ``fmt``, loaded by the first thread that writes to it. The format is checked
    now; importing the sink (Parquet alone is about 15 ms) happens on a writer thread, while the
    first tables are being generated, instead of before the first row."""

    def __init__(self, fmt: str) -> None:
        _check_format(fmt)
        self._fmt = fmt
        self._sink: Any = None
        self._lock = threading.Lock()

    def loaded(self) -> Any:
        with self._lock:
            if self._sink is None:
                self._sink = _sink(self._fmt)
            return self._sink

    def target(self, output_dir: Path, table: str) -> Path:
        return _target(self._fmt, self.loaded(), output_dir, table)

    def write(self, *args: Any, **kwargs: Any) -> Any:
        return self.loaded().write(*args, **kwargs)


def _target(fmt: str, sink: Any, output_dir: Path, table: str) -> Path:
    # A sink with an empty extension (Delta) writes <output_dir>/<table>/ itself; the others write
    # <output_dir>/<table>.<extension>.
    from shape.security.names import contained

    extension = _extension(fmt, sink)
    return output_dir if not extension else contained(output_dir, table, f".{extension}")


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


def _paths(fmt: str, sink: Any, output_dir: Path, tables: list[str]) -> list[Path]:
    directory = not _extension(fmt, sink)
    return [output_dir / t if directory else _target(fmt, sink, output_dir, t) for t in tables]


def _check_destination(sink: Any, output_dir: str | Path) -> Path:
    """The output directory as a path, after the sink has accepted its URI scheme: a path made
    from ``abfss://account/dir`` would otherwise be created as a local folder called ``abfss:``.
    A local destination does not load a lazy sink (that happens on a writer thread)."""
    text = str(output_dir)
    if uri_scheme(text) != "file":
        require_scheme(sink.loaded() if isinstance(sink, _LazySink) else sink, text)
    return local_path(text)


_WORKBOOK_OPTIONS = ("chaos_log", "drift_plan", "autofilter")


def _write_workbook(
    result: GenerationResult,
    out: Path,
    options: Mapping[str, Any],
    seed: int | None = None,
    scale: str | None = None,
) -> list[Path]:
    """``excel``: one workbook, ``<domain>.xlsx``, a sheet per table and a ``_README`` sheet. The
    ``chaos_log`` and ``drift_plan`` options name the files whose planted changes it lists;
    ``autofilter=False`` leaves the header row without a filter."""
    from shape.security.names import contained

    schema = result.schema
    model = schema.model
    out.mkdir(parents=True, exist_ok=True)
    target = contained(out, str(options.get("workbook") or model.domain or "workbook"), ".xlsx")
    keys: dict[str, list[str]] = {
        name: [
            c.name for c in tdef.columns.values() if c.name in tdef.primary_key or c.is_foreign_key
        ]
        for name, tdef in schema.tables.items()
    }
    meta = {
        "domain": model.domain or model.name,
        "schema_mode": model.schema_mode,
        "seed": seed if seed is not None else model.seed,
        "scale": scale or schema.generation.scale,
        "elapsed_seconds": round(result.elapsed_seconds, 3),
    }
    _sink("excel").write_workbook(
        str(target),
        {name: result.tables[name] for name in result.generation_order},
        meta=meta,
        identifier_columns_by_table=keys,
        **{k: options[k] for k in _WORKBOOK_OPTIONS if options.get(k) is not None},
    )
    return [target]


def write_result(
    result: GenerationResult,
    fmt: str,
    output_dir: str | Path,
    *,
    max_workers: int | None = None,
    **options: Any,
) -> list[Path]:
    """Write every table of ``result`` as ``fmt``; return the files (or Delta directories). The
    ``excel`` format is one workbook for all the tables."""
    if fmt == "excel":
        return _write_workbook(result, Path(output_dir), options)
    sink = _sink(fmt)
    out = _check_destination(sink, output_dir)
    out.mkdir(parents=True, exist_ok=True)
    names = list(result.generation_order)

    def one(name: str) -> None:
        table = result.tables[name]
        sink.write(
            str(_target(fmt, sink, out, name)),
            name,
            iter(table.to_batches()),
            schema=table.schema,
            **_options(fmt, result.schema, name, options),
        )

    _run_parallel(names, one, _writers(max_workers))
    return _paths(fmt, sink, out, names)


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
    if schema.generation.output.get("copula_mixed"):
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
    sink = _LazySink(fmt)
    out = _check_destination(sink, output_dir)
    out.mkdir(parents=True, exist_ok=True)
    queues: dict[str, queue.Queue[pa.RecordBatch | None]] = {}

    def write(name: str, batches: Iterator[pa.RecordBatch], schema: pa.Schema | None) -> None:
        extra = {"schema": schema} if schema is not None else {}
        sink.write(
            str(sink.target(out, name)),
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
    return _paths(fmt, sink.loaded(), out, list(result.generation_order))


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
    if fmt == "excel":  # one workbook for all the tables: generated whole
        return _write_workbook(engine.generate(), Path(output_dir), options, engine.seed)
    if needs_post_pass(engine.schema):
        return _write_overlapped(engine, fmt, output_dir, _writers(max_workers), options)
    engine.schema.validate_or_raise()
    sink = _LazySink(fmt)
    out = _check_destination(sink, output_dir)
    out.mkdir(parents=True, exist_ok=True)
    names = list(engine.order)

    def one(name: str) -> None:
        sink.write(
            str(sink.target(out, name)),
            name,
            _prefetch(engine.iter_chunks(name, chunk_rows)),
            **_options(fmt, engine.schema, name, options),
        )

    _run_parallel(names, one, _writers(max_workers))
    return _paths(fmt, sink.loaded(), out, names)


# ---- targets by URI (abfss://, delta+abfss://, mssql://, postgresql://, ...) ------------------

_FILE_SINKS = frozenset({"abfss", "delta"})
_FILE_MODES = {
    "overwrite": "overwrite",
    "replace": "overwrite",
    "truncate": "overwrite",
    "append": "append",
    "fail": "fail",
    "create": "fail",
}
_DB_MODES = {
    "create": "create",
    "fail": "create",
    "append": "append",
    "truncate": "truncate",
    "replace": "replace",
    "overwrite": "replace",
}


class TargetOptions:
    """What the command line says about how to write; each sink takes what it understands."""

    def __init__(
        self,
        *,
        fmt: str = "parquet",
        formats: Mapping[str, str] | None = None,
        path_template: str | None = None,
        batch_date: str | None = None,
        roll_rows: int | None = None,
        roll_seconds: float | None = None,
        commit_rows: int | None = None,
        commit_seconds: float | None = None,
        write_mode: str | None = None,
        manifest: bool = False,
        partition_by: list[str] | None = None,
        extra: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        self.fmt = fmt
        self.formats = dict(formats or {})
        self.path_template = path_template
        self.batch_date = batch_date
        self.roll_rows = roll_rows
        self.roll_seconds = roll_seconds
        self.commit_rows = commit_rows
        self.commit_seconds = commit_seconds
        self.write_mode = write_mode
        self.manifest = manifest
        self.partition_by = partition_by
        self.extra = {k: dict(v) for k, v in (extra or {}).items()}

    def for_sink(self, name: str, schema: GenSchema | None, table: str) -> dict[str, Any]:
        out: dict[str, Any] = {}

        def put(key: str, value: Any) -> None:
            if value not in (None, False, [], {}):
                out[key] = value

        if name == "abfss":
            put("format", self.fmt)
            put("formats", self.formats)
            put("path_template", self.path_template)
            put("batch_date", self.batch_date)
            put("roll_rows", self.roll_rows)
            put("roll_seconds", self.roll_seconds)
            put("manifest", self.manifest)
            if self.write_mode:
                out["mode"] = _file_mode(self.write_mode)
        elif name == "delta":
            put("commit_rows", self.commit_rows)
            put("commit_seconds", self.commit_seconds)
            put("partition_by", self.partition_by)
            if self.write_mode:
                mode = _file_mode(self.write_mode)
                if mode == "fail":
                    raise ValueError("a Delta target takes --write-mode overwrite or append")
                out["mode"] = mode
        else:
            put("commit_rows", self.commit_rows)
            if self.write_mode:
                db_mode = _DB_MODES.get(self.write_mode)
                if db_mode is None:
                    raise ValueError(f"unknown --write-mode {self.write_mode!r} for a database")
                out["write_mode"] = db_mode
            if schema is not None:
                db = sql_options(schema, table)
                out["primary_key"] = db["primary_key"]
                out["columns"] = db["columns"]
        out.update(self.extra.get(name, {}))
        return out


def _file_mode(mode: str) -> str:
    try:
        return _FILE_MODES[mode]
    except KeyError:
        raise ValueError(
            f"unknown --write-mode {mode!r}: overwrite, append or fail for files"
        ) from None


def _feed(
    sink: Any, uri: str, table: str, pending: queue.Queue[Any], options: dict[str, Any]
) -> int:
    def batches() -> Iterator[pa.RecordBatch]:
        while (item := pending.get()) is not None:
            if isinstance(item, BaseException):
                raise item
            yield item

    return int(sink.write(uri, table, batches(), **options))


def write_targets(
    engine: Engine,
    uris: list[str],
    options: TargetOptions,
    *,
    chunk_rows: int | None = None,
    max_workers: int | None = None,
) -> dict[str, dict[str, int]]:
    """Generate every table once and write it to each target URI (fan-out): ``{uri: {table:
    rows}}``. Each target's sink is the one that registered the URI's scheme."""
    from shape.io.targets import redact, sink_for_target

    engine.reserved_cores = max(engine.reserved_cores, 1)
    sinks = [(uri, *sink_for_target(uri)) for uri in uris]
    engine.schema.validate_or_raise()
    post = needs_post_pass(engine.schema)
    result = engine.generate() if post else None
    names = list(result.generation_order if result is not None else engine.order)
    written: dict[str, dict[str, int]] = {redact(u): {} for u in uris}
    lock = threading.Lock()

    def one(name: str) -> None:
        schema: pa.Schema | None = None
        if result is not None:
            table = result.tables[name]
            schema = table.schema
            source: Iterator[pa.RecordBatch] = iter(table.to_batches())
        else:
            source = _prefetch(engine.iter_chunks(name, chunk_rows))
        queues = [queue.Queue[Any](maxsize=_QUEUE_DEPTH) for _ in sinks]
        counts: list[int] = [0] * len(sinks)
        errors: list[BaseException] = []

        def run(i: int) -> None:
            uri, sink_name, sink = sinks[i]
            opts = options.for_sink(sink_name, engine.schema, name)
            if schema is not None:
                opts["schema"] = schema
            try:
                counts[i] = _feed(sink, uri, name, queues[i], opts)
            except BaseException as exc:
                errors.append(exc)
                while queues[i].get() is not None:  # keep the producer from blocking on us
                    pass

        threads = [threading.Thread(target=run, args=(i,), daemon=True) for i in range(len(sinks))]
        for t in threads:
            t.start()
        try:
            for batch in source:
                for q in queues:
                    q.put(batch)
        except BaseException as exc:
            for q in queues:
                q.put(exc)
            raise
        finally:
            for q in queues:
                q.put(None)
            for t in threads:
                t.join()
        if errors:
            raise errors[0]
        with lock:
            for (uri, _, _), rows in zip(sinks, counts, strict=True):
                written[redact(uri)][name] = rows

    _run_parallel(names, one, _writers(max_workers))
    return written
