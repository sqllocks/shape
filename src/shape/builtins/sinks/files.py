"""File sinks: ``RecordBatch``es for one table to a local file.

``uri`` is a path or ``file://`` URI. When it names an existing directory (or ends with a
separator) the file is ``<table>.<extension>`` inside it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.csv as pacsv  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from shape.builtins.sources.files import local_path


class _FileSink:
    name = ""
    schemes = ("file",)
    extension = ""

    def _target(self, uri: str, table: str) -> Path:
        path = local_path(uri)
        if path.is_dir() or uri.endswith(("/", "\\")):
            path.mkdir(parents=True, exist_ok=True)
            return path / f"{table}.{self.extension}"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def write(self, uri: str, table: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        target = self._target(uri, table)
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

    def _open(self, target: Path, schema: pa.Schema, options: dict[str, Any]) -> Any:
        raise NotImplementedError

    def _write(self, writer: Any, batch: pa.RecordBatch) -> None:
        writer.write_batch(batch)


class CsvSink(_FileSink):
    name = "csv"
    extension = "csv"

    def _open(self, target: Path, schema: pa.Schema, options: dict[str, Any]) -> Any:
        delimiter = options.get("delimiter", ",")
        return pacsv.CSVWriter(
            str(target), schema, write_options=pacsv.WriteOptions(delimiter=delimiter)
        )


class ParquetSink(_FileSink):
    name = "parquet"
    extension = "parquet"

    def _open(self, target: Path, schema: pa.Schema, options: dict[str, Any]) -> Any:
        return pq.ParquetWriter(str(target), schema, compression=options.get("compression", "zstd"))


class IpcSink(_FileSink):
    name = "ipc"
    extension = "arrow"

    def _open(self, target: Path, schema: pa.Schema, options: dict[str, Any]) -> Any:
        return pa.ipc.new_file(str(target), schema)


class _JsonlWriter:
    def __init__(self, target: Path) -> None:
        self._f = target.open("w", encoding="utf-8")

    def write_batch(self, batch: pa.RecordBatch) -> None:
        for row in batch.to_pylist():
            self._f.write(json.dumps(row, separators=(",", ":"), ensure_ascii=False, default=str))
            self._f.write("\n")

    def close(self) -> None:
        self._f.close()


class JsonlSink(_FileSink):
    name = "jsonl"
    extension = "jsonl"

    def _open(self, target: Path, schema: pa.Schema, options: dict[str, Any]) -> Any:
        return _JsonlWriter(target)
