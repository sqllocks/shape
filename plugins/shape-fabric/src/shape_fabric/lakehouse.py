"""Lakehouse Files writer: tables as Parquet, CSV or JSON-lines files in a folder.

A folder is a local directory, or a OneLake / ADLS Gen2 location (``abfss://...``, or
``onelake://<workspace>/<lakehouse>/Files/<path>``). Each table becomes ``<folder>/<table>/
part-0001.<ext>``; the landing-zone helpers in :mod:`shape_fabric.onelake` build the
partitioned folders, and :meth:`LakehouseWriter.write_manifest` / :meth:`write_done_flag` write
the control files next to them, to OneLake too.

    writer = LakehouseWriter("onelake://MyWorkspace/MyLakehouse/Files/raw", credential=cred)
    result = writer.write_tables({"customer": batches_of_customer})

Batches are streamed: memory does not grow with the table. A file is complete or absent (see
:mod:`shape_fabric._storage`). Writing the same table again replaces its file.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Iterator, Mapping
from typing import Any, BinaryIO

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.csv as pacsv  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from shape.errors import ShapeError
from shape.streaming.emit.formats import rows_of

from . import onelake
from ._storage import Storage
from .errors import WriteError, WriteResult

FORMATS = ("parquet", "csv", "jsonl")


def _check_format(fmt: str) -> str:
    fmt = str(fmt).lower()
    if fmt not in FORMATS:
        raise ShapeError(f"unknown file format {fmt!r}; choose one of {', '.join(FORMATS)}")
    return fmt


def _jsonl_line(row: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(row, separators=(",", ":"), ensure_ascii=False, allow_nan=False, default=str)
        + "\n"
    ).encode("utf-8")


class _Counting:
    """Batches passing through; ``rows`` is how many were seen."""

    def __init__(self, batches: Iterable[pa.RecordBatch]) -> None:
        self._it = iter(batches)
        self.rows = 0
        self.schema: pa.Schema | None = None

    def __iter__(self) -> Iterator[pa.RecordBatch]:
        for batch in self._it:
            if self.schema is None:
                self.schema = batch.schema
            self.rows += batch.num_rows
            yield batch


def write_batches(
    handle: BinaryIO, fmt: str, batches: Iterable[pa.RecordBatch], schema: pa.Schema
) -> None:
    """Write ``batches`` to an open binary ``handle`` in ``fmt`` (``schema`` is the first
    batch's)."""
    if fmt == "parquet":
        with pq.ParquetWriter(handle, schema, compression="snappy") as writer:
            for batch in batches:
                writer.write_batch(batch)
    elif fmt == "csv":
        with pacsv.CSVWriter(handle, schema) as csv_writer:
            for batch in batches:
                csv_writer.write_batch(batch)
    else:
        for batch in batches:
            handle.write(b"".join(_jsonl_line(r) for r in rows_of(batch)))


class LakehouseWriter:
    """Tables to files in a lakehouse ``Files`` folder (or any local or ADLS folder)."""

    def __init__(
        self,
        folder: str,
        *,
        format: str = "parquet",
        credential: Any = None,
        filesystem: Any = None,
        storage: Storage | None = None,
    ) -> None:
        self.folder = folder.rstrip("/") if onelake.is_remote(folder) else folder
        self.format = _check_format(format)
        self.storage = storage or Storage(credential=credential, filesystem=filesystem)

    # ------------------------------------------------------------------ files
    def write_table(
        self,
        table: str,
        batches: Iterable[pa.RecordBatch],
        *,
        format: str | None = None,
        directory: str | None = None,
        file_name: str | None = None,
        schema: pa.Schema | None = None,
    ) -> int:
        """Write one table; return its row count.

        ``directory`` replaces ``<folder>/<table>`` (a landing-zone partition, for example);
        ``file_name`` replaces ``part-0001.<ext>``; ``schema`` is needed only when ``batches``
        may be empty.
        """
        fmt = _check_format(format or self.format)
        target_dir = directory or onelake.join(self.folder, table)
        name = onelake.segment(file_name or f"part-0001.{fmt}", "file name")
        stream = _Counting(batches)
        iterator = iter(stream)
        first = next(iterator, None)
        use_schema = stream.schema or schema
        if use_schema is None:
            raise ShapeError(
                f"table {table!r} has no batches and no schema: pass schema= to write an empty file"
            )

        def chained() -> Iterator[pa.RecordBatch]:
            if first is not None:
                yield first
            yield from iterator

        path = onelake.join(target_dir, name)
        self.storage.write(path, lambda h: write_batches(h, fmt, chained(), use_schema))
        return stream.rows

    def write_tables(
        self, tables: Mapping[str, Iterable[pa.RecordBatch]], **options: Any
    ) -> WriteResult:
        """Write every table; stops at the first failure with a :class:`WriteError` whose
        ``result`` lists the tables that were written."""
        start = time.monotonic()
        result = WriteResult(self.folder)
        for table, batches in tables.items():
            try:
                result.per_table[table] = self.write_table(table, batches, **options)
            except WriteError:
                raise
            except Exception as exc:
                result.elapsed_seconds = time.monotonic() - start
                raise WriteError(
                    f"writing {table!r} to {self.folder} failed: {exc}", result
                ) from exc
        result.elapsed_seconds = time.monotonic() - start
        return result

    # ---------------------------------------------------------- control files
    def write_manifest(self, path: str, manifest: Mapping[str, Any]) -> str:
        """Write a JSON manifest (sorted keys, so the bytes depend only on the content)."""
        text = json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n"
        self.storage.write_bytes(path, text.encode("utf-8"))
        return path

    def write_done_flag(self, path: str) -> str:
        """Write an empty sentinel file (``_SUCCESS``)."""
        self.storage.write_bytes(path, b"")
        return path

    # -------------------------------------------------------------- layout
    def landing_zone(self, domain: str, entity: str, dt: str, hour: str | int | None = None) -> str:
        return onelake.landing_zone(self.folder, domain, entity, dt, hour)

    def manifest_path(self, domain: str, entity: str, dt: str) -> str:
        return onelake.manifest(self.folder, domain, entity, dt)

    def done_flag_path(self, domain: str, entity: str, dt: str) -> str:
        return onelake.done_flag(self.folder, domain, entity, dt)
