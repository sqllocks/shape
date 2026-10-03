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

import base64
import contextlib
import datetime as dt
import io
import json
import math
import os
import shutil
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.csv as pacsv  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from shape.builtins.sources.files import local_path
from shape.io.landing import render_path
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
        if target.exists() and not target.is_file():  # a device or a pipe: nothing to replace
            return self._write_file(target, batches, options)
        final = Path(os.path.realpath(target)) if target.is_symlink() else target
        # Written under a temporary name and renamed when complete, so a failed or interrupted
        # run keeps the previous file and a reader never sees a partial one.
        temp = final.with_name(f".shape-{uuid.uuid4().hex[:12]}.tmp")
        try:
            rows = self._write_file(temp, batches, options)
            if final.exists():
                shutil.copymode(final, temp)
            os.replace(temp, final)
        except BaseException:
            with contextlib.suppress(OSError):
                temp.unlink()
            raise
        return rows

    def _write_file(
        self, target: Path, batches: Iterable[pa.RecordBatch], options: dict[str, Any]
    ) -> int:
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


class ParquetSink(_FileSink):
    """Parquet files. Option ``fingerprint`` (``True``, or the run's context: ``dataset_id``,
    ``reproducibility``, ``profile_content_id``) holds the table in memory, computes its
    ``table_id`` and writes the signed-format fingerprint (``shape.fingerprint``, see
    ``docs/FINGERPRINT.md``) into the footer; it does not combine with rolling files."""

    name = "parquet"
    extension = "parquet"

    def write(self, uri: str, table: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        stamp = options.get("fingerprint")
        if not stamp:
            return super().write(uri, table, batches, **options)
        from shape.builtins.sinks._roll import wants_rolling
        from shape.fingerprint import KEY, dump, for_sink

        if wants_rolling(options):
            raise ValueError("fingerprint does not combine with rolling files (roll_rows/seconds)")
        schema = options.get("schema")
        listed = list(batches)
        if listed:
            schema = listed[0].schema
        if schema is None:
            raise ValueError(
                "a fingerprinted Parquet file needs a schema when there are no batches"
            )
        whole = pa.Table.from_batches(listed, schema=schema)
        doc = for_sink(table, whole, stamp)
        whole = whole.replace_schema_metadata(
            {**(schema.metadata or {}), KEY.encode(): dump(doc).encode("utf-8")}
        )
        rest = {k: v for k, v in options.items() if k not in ("fingerprint", "schema")}
        return super().write(uri, table, iter(whole.to_batches()), schema=whole.schema, **rest)

    def _open(self, target: Any, schema: pa.Schema, options: dict[str, Any]) -> Any:
        # T-17: snappy, dictionary encoding on.
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
    """Dates and times as ISO 8601; decimals as exact strings (a JSON number would round);
    binary as base64 (as the stream formats write it)."""
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(value)).decode("ascii")
    return str(value)


def _finite(value: Any) -> Any:
    """``value`` with every non-finite float (NaN, Infinity: not JSON) replaced by ``None``."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(v) for v in value]
    return value


def _json_line(row: dict[str, Any]) -> str:
    try:
        return json.dumps(
            row, separators=(",", ":"), ensure_ascii=False, allow_nan=False, default=_json_default
        )
    except ValueError:  # a NaN or an infinity somewhere in the row
        return json.dumps(
            _finite(row),
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
            default=_json_default,
        )


class _JsonlWriter:
    def __init__(self, target: Any) -> None:
        self._binary = hasattr(target, "write")
        if self._binary:
            self._f: Any = io.TextIOWrapper(target, encoding="utf-8", newline="\n")
        else:
            self._f = Path(target).open("w", encoding="utf-8")

    def write_batch(self, batch: pa.RecordBatch) -> None:
        for row in batch.to_pylist():
            self._f.write(_json_line(row))
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
