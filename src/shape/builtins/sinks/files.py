"""File sinks: ``RecordBatch``es for one table to a local file.

``uri`` is a path or ``file://`` URI. When it names an existing directory (or ends with a
separator) the file is ``<table>.<extension>`` inside it.

With ``roll_rows`` and/or ``roll_seconds`` (micro-batch mode) ``uri`` is a directory and the
table is written as a series of files, ``{table}/{table}-{part}.{ext}`` unless
``path_template`` says otherwise. Each file is written under a temporary name and renamed when
complete, so a reader sees only whole files, and rows are readable while the stream is still
running (see :mod:`shape.builtins.sinks._roll`). ``mode`` is ``overwrite``, ``append`` or ``fail``.

With the option ``path_template`` (and ``batch_date``, ``YYYY-MM-DD``, when the template has a date
token) ``uri`` is the landing root and the file is the template filled for the table, for example
``{table}/ingest_date={date}/{table}_{yyyymmdd}.{ext}`` (see :mod:`shape.io.landing`). Folders are
created as needed.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.csv as pacsv  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from shape.builtins.sources.files import local_path
from shape.io.landing import render_path
from shape.kernel.dispatch import get_kernel
from shape.plugins.schemes import require_scheme

DEFAULT_ROLL_TEMPLATE = "{table}/{table}-{part}.{ext}"


class _FileSink:
    name = ""
    schemes = ("file",)
    extension = ""

    def _target(self, uri: str, table: str, options: dict[str, Any] | None = None) -> Path:
        path = local_path(uri)
        template = (options or {}).get("path_template")
        if template:
            root = path.resolve()
            name = render_path(template, table, self.extension, (options or {}).get("batch_date"))
            target = (root / name).resolve()
            if not target.is_relative_to(root):
                raise ValueError(f"path template {template!r} leaves the landing root")
            target.parent.mkdir(parents=True, exist_ok=True)
            return target
        if path.is_dir() or uri.endswith(("/", "\\")):
            path.mkdir(parents=True, exist_ok=True)
            from shape.security.names import contained

            return contained(path, table, f".{self.extension}")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def open_table(
        self, uri: str, table: str, schema: pa.Schema | None = None, **options: Any
    ) -> Any:
        """A streaming writer for ``table`` (``write_batch``, ``flush``, ``close``): files roll as
        the options say (every batch is one file when neither threshold is given)."""
        from shape.builtins.sinks._roll import RollingTableWriter
        from shape.io.store import LocalStore

        template = options.get("path_template") or DEFAULT_ROLL_TEMPLATE
        return RollingTableWriter(
            LocalStore(local_path(uri)), table, self, options, template=template
        )

    def write(self, uri: str, table: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        require_scheme(self, uri)
        from shape.builtins.sinks._roll import wants_rolling

        if wants_rolling(options):
            rolling = self.open_table(uri, table, **options)
            try:
                rolling.write_all(batches)
            except BaseException:
                rolling.abort()
                raise
            return int(rolling.close(schema=options.get("schema")))
        target = self._target(uri, table, options)
        rows = 0
        writer: Any = None
        try:
            for batch in batches:
                if writer is None:
                    writer = self._open(target, batch.schema, options)
                rows += batch.num_rows
                self._write(writer, batch)
            if writer is None:  # no batches: still produce a valid, empty file
                writer = self._open(target, options.get("schema") or pa.schema([]), options)
        finally:
            if writer is not None:
                writer.close()
        return rows

    def _open(self, target: Any, schema: pa.Schema, options: dict[str, Any]) -> Any:
        """A writer for ``target``: a path, or an open binary file (rolling and cloud files)."""
        raise NotImplementedError

    def _write(self, writer: Any, batch: pa.RecordBatch) -> None:
        writer.write_batch(batch)


def _sink_arg(target: Any) -> Any:
    """A path as ``str``; an open file as it is."""
    return target if hasattr(target, "write") else str(target)


class CsvSink(_FileSink):
    name = "csv"
    extension = "csv"

    def _open(self, target: Any, schema: pa.Schema, options: dict[str, Any]) -> Any:
        delimiter = options.get("delimiter", ",")
        return pacsv.CSVWriter(
            _sink_arg(target), schema, write_options=pacsv.WriteOptions(delimiter=delimiter)
        )


class TsvSink(CsvSink):
    name = "tsv"
    extension = "tsv"

    def _open(self, target: Any, schema: pa.Schema, options: dict[str, Any]) -> Any:
        return super()._open(target, schema, {**options, "delimiter": "\t"})


# A group is encoded when it is complete, so a table written while it is generated waits for this
# many rows before the encoder starts; 256k rows is a few chunks, and the file is 0.7% larger than
# one with groups of a million rows (measured on the retail medium tables).
ROW_GROUP_ROWS = 1 << 18
# Dictionary encoding stays on (T-17); a column whose dictionary outgrows this many bytes stops
# using it, so a column of mostly distinct values (a key, an amount) is not hashed for a megabyte
# of dictionary first. 128 KiB holds a dictionary of 16k distinct 8-byte values.
DICTIONARY_PAGE_BYTES = 128 * 1024


class _ParquetRowGroups:
    """Writes the batches it is given in row groups of up to ``ROW_GROUP_ROWS`` rows: a batch of
    64k rows per row group would repeat the dictionaries and the page headers sixteen times as
    often and write a larger file."""

    def __init__(self, writer: pq.ParquetWriter, row_group_rows: int) -> None:
        self._writer = writer
        self._limit = row_group_rows
        self._pending: list[pa.RecordBatch] = []
        self._rows = 0

    def write_batch(self, batch: pa.RecordBatch) -> None:
        self._pending.append(batch)
        self._rows += batch.num_rows
        if self._rows >= self._limit:
            self._flush()

    def _flush(self) -> None:
        if self._pending:
            self._writer.write_table(pa.Table.from_batches(self._pending))
        self._pending, self._rows = [], 0

    def close(self) -> None:
        try:
            self._flush()
        finally:
            self._writer.close()


PARQUET_WRITER_ENV = "SHAPE_PARQUET_WRITER"
_NATIVE_CODECS = {"snappy", "none", "uncompressed"}


def _native_writer(schema: pa.Schema, options: dict[str, Any]) -> Any:
    """The native writer class when it can write this file, else ``None``: the pure-Python kernel
    has none, the native one writes snappy or no compression and flat columns only, and
    ``SHAPE_PARQUET_WRITER=pyarrow`` turns it off."""
    if os.environ.get(PARQUET_WRITER_ENV, "auto").strip().lower() == "pyarrow":
        return None
    cls = getattr(get_kernel(), "ParquetOut", None)
    if cls is None or not schema.names:
        return None
    if str(options.get("compression", "snappy")).lower() not in _NATIVE_CODECS:
        return None
    return cls


def _encode_in_parallel(options: dict[str, Any]) -> bool:
    """Whether the native writer may use several threads: not when the caller asked for one thread
    (``SHAPE_THREADS=1``, or the ``threads`` option) or the machine has one core."""
    if "threads" in options:
        return int(options["threads"]) != 1
    from shape.generation.engine import worker_threads

    return worker_threads() > 1


class ParquetSink(_FileSink):
    name = "parquet"
    extension = "parquet"

    def open_native(self, target: Any, schema: pa.Schema, options: dict[str, Any]) -> Any:
        """The native writer for ``target`` (``write_batch``, ``finish``, ``wait``, ``close``), or
        ``None`` when it cannot write this file (see ``_native_writer``); the caller then writes it
        through :meth:`write`. ``finish`` returns at once and the file is complete after ``wait``,
        so a caller that streams many tables need not wait for each before it starts the next."""
        native = _native_writer(schema, options)
        use_dictionary = options.get("use_dictionary", True)
        if native is None or not isinstance(use_dictionary, bool):
            return None
        try:
            return native(
                str(target),
                schema,
                row_group_rows=int(options.get("row_group_rows", ROW_GROUP_ROWS)),
                compression=str(options.get("compression", "snappy")).lower(),
                use_dictionary=use_dictionary,
                dictionary_page_bytes=int(
                    options.get("dictionary_page_bytes", DICTIONARY_PAGE_BYTES)
                ),
                parallel=_encode_in_parallel(options),
            )
        except ValueError:  # a type the native writer does not do
            return None

    def _open(self, target: Any, schema: pa.Schema, options: dict[str, Any]) -> Any:
        # T-17: snappy, dictionary encoding on. The native writer encodes the row groups and
        # columns of the file on several threads; pyarrow's writer (the twin) on one. An open
        # file (rolling and cloud files) goes through pyarrow's writer.
        native = None if hasattr(target, "write") else self.open_native(target, schema, options)
        if native is not None:
            return native
        writer = pq.ParquetWriter(
            _sink_arg(target),
            schema,
            compression=options.get("compression", "snappy"),
            use_dictionary=options.get("use_dictionary", True),
            dictionary_pagesize_limit=int(
                options.get("dictionary_page_bytes", DICTIONARY_PAGE_BYTES)
            ),
        )
        return _ParquetRowGroups(writer, int(options.get("row_group_rows", ROW_GROUP_ROWS)))


class IpcSink(_FileSink):
    name = "ipc"
    extension = "arrow"

    def _open(self, target: Any, schema: pa.Schema, options: dict[str, Any]) -> Any:
        return pa.ipc.new_file(_sink_arg(target), schema)


def _json_default(value: Any) -> str:
    """Dates and times as ISO 8601; decimals as exact strings (a JSON number would round)."""
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    return str(value)


class _JsonlWriter:
    def __init__(self, target: Any) -> None:
        self._binary = hasattr(target, "write")
        if self._binary:
            self._f: Any = io.TextIOWrapper(target, encoding="utf-8", newline="\n")
        else:
            self._f = Path(target).open("w", encoding="utf-8")

    def write_batch(self, batch: pa.RecordBatch) -> None:
        for row in batch.to_pylist():
            self._f.write(
                json.dumps(row, separators=(",", ":"), ensure_ascii=False, default=_json_default)
            )
            self._f.write("\n")

    def close(self) -> None:
        if self._binary:  # the caller owns the file
            self._f.flush()
            self._f.detach()
        else:
            self._f.close()


class JsonlSink(_FileSink):
    name = "jsonl"
    extension = "jsonl"

    def _open(self, target: Any, schema: pa.Schema, options: dict[str, Any]) -> Any:
        return _JsonlWriter(target)
