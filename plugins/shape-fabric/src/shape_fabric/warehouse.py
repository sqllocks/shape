"""Warehouse bulk writer: stage Parquet in OneLake, then ``COPY INTO``.

    writer = WarehouseWriter(
        connection_string, staging_path="onelake://MyWorkspace/MyLakehouse/Files", credential=cred
    )
    writer.write_table("customer", batches, write_mode="create")

For each table the writer (1) prepares the table as :mod:`shape_fabric.sqldb` does (same
``write_mode`` values, same safe default), (2) writes the rows as Parquet files of at most
``chunk_rows`` rows under ``<staging_path>/staging/<run>/<table>/`` (timestamps in microseconds,
which is all the Warehouse reads), (3) runs one ``COPY INTO`` over that folder, using the
``https://onelake.dfs.fabric.microsoft.com/...`` form the statement requires, and (4) deletes the
staged files, **also when a step failed**.

``truncate`` and ``replace`` commit with the rows, exactly as in :mod:`shape_fabric.sqldb`: a
failed write keeps the old rows of an existing table, and a table this call created is dropped
again (#429).

The number of rows ``COPY INTO`` loaded must equal the number staged; if it differs the write
fails (and a table this call created is dropped). The count is the statement's own; where the
driver does not report one, rows are counted before and after.

``staging_path`` is the ``Files`` folder of a lakehouse in the same workspace as the Warehouse,
or a folder in it (``abfss://`` or ``onelake://``). ``credential`` signs in both to the Warehouse
and to OneLake.
"""

from __future__ import annotations

import re
import uuid
import warnings
import zlib
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from typing import Any, BinaryIO

import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError

from . import _tsql, onelake
from ._storage import Storage
from .errors import WriteError, WriteResult
from .lakehouse import write_batches
from .sqldb import SqlConnection, check_mode, prepare_table, undo, write_many

DEFAULT_CHUNK_ROWS = 1_000_000
_SAFE_URL = re.compile(r"https://[A-Za-z0-9.\-]+/[A-Za-z0-9._~%@:/ \-]+")


def copy_literal(url: str) -> str:
    """``url`` as the single-quoted literal of a ``COPY INTO ... FROM`` clause. A location
    cannot be a parameter, so it is checked against a strict character set (no quote,
    semicolon, comment marker or control character can get through) and the quotes are doubled
    as well."""
    if not _SAFE_URL.fullmatch(url) or "--" in url or "/*" in url:
        raise ShapeError(f"not a usable staging location for COPY INTO: {url!r}")
    return "'" + url.replace("'", "''") + "'"


def copy_into_sql(schema_name: str, table: str, folder_url: str) -> str:
    # the table name is quoted by _tsql.qualified; the location passed copy_literal's check
    return (
        f"COPY INTO {_tsql.qualified(schema_name, table)} "
        f"FROM {copy_literal(folder_url)} WITH (FILE_TYPE = 'PARQUET')"
    )  # nosec B608


def staging_slug(table: str) -> str:
    """A folder name for ``table`` made of safe characters (and a checksum, so two names that
    differ only in unusual characters stay apart)."""
    return re.sub(r"[^A-Za-z0-9_.]", "_", table)[:60] + f"-{zlib.crc32(table.encode()):08x}"


def chunked(batches: Iterable[pa.RecordBatch], chunk_rows: int) -> Iterator[list[pa.RecordBatch]]:
    """``batches`` regrouped into lists of at most ``chunk_rows`` rows (batches are sliced)."""
    group: list[pa.RecordBatch] = []
    size = 0
    for batch in batches:
        offset = 0
        while offset < batch.num_rows:
            take = min(chunk_rows - size, batch.num_rows - offset)
            group.append(batch.slice(offset, take))
            size += take
            offset += take
            if size >= chunk_rows:
                yield group
                group, size = [], 0
    if group:
        yield group


class WarehouseWriter:
    """Tables into a Fabric Warehouse by ``COPY INTO``; see the module docstring."""

    def __init__(
        self,
        connection_string: str | None = None,
        staging_path: str | None = None,
        *,
        credential: Any = None,
        connection: Any = None,
        connect: Callable[..., Any] | None = None,
        filesystem: Any = None,
        storage: Storage | None = None,
        schema_name: str = "dbo",
        run_id: str | None = None,
    ) -> None:
        if not staging_path:
            raise ShapeError(
                "a Warehouse bulk load needs staging_path: a lakehouse Files folder in OneLake "
                "(abfss://... or onelake://<workspace>/<lakehouse>/Files)"
            )
        if not onelake.is_remote(staging_path):
            raise ShapeError("staging_path must be a OneLake path (abfss:// or onelake://)")
        self.staging = onelake.parse(staging_path)
        self.db = SqlConnection(connection_string, credential, connection, connect, True)
        self.storage = storage or Storage(credential=credential, filesystem=filesystem)
        self.schema_name = schema_name
        self.run_id = run_id or uuid.uuid4().hex[:12]

    @property
    def destination(self) -> str:
        return _tsql.redact(self.db.connection_string or "<open connection>")

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> WarehouseWriter:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _folder(self, table: str) -> onelake.OneLakePath:
        return self.staging.join("staging", self.run_id, staging_slug(table))

    def write_table(
        self,
        table: str,
        batches: Iterable[pa.RecordBatch],
        *,
        write_mode: str = "create",
        chunk_rows: int = DEFAULT_CHUNK_ROWS,
        schema_name: str | None = None,
        columns: Mapping[str, Mapping[str, Any]] | None = None,
        primary_key: Sequence[str] = (),
        schema: pa.Schema | None = None,
    ) -> int:
        """Write one table; return the rows loaded."""
        mode = check_mode(write_mode)
        if isinstance(chunk_rows, bool) or not isinstance(chunk_rows, int) or chunk_rows < 1:
            raise ShapeError("chunk_rows must be a positive integer")
        sname = schema_name or self.schema_name
        _tsql.qualified(sname, table)
        stream = iter(batches)
        first = next(stream, None)
        use_schema = first.schema if first is not None else schema
        if use_schema is None:
            raise ShapeError(
                f"table {table!r} has no batches and no schema: pass schema= to create it empty"
            )
        folder = self._folder(table)
        created = False
        db = self.db
        try:
            created = self._prepare(sname, table, mode, use_schema, columns, primary_key)
            if first is None:
                return 0
            staged = self._stage(table, folder, first, stream, chunk_rows)
            loaded = self._copy(sname, table, folder, mode, created, staged)
            if loaded != staged:
                raise ShapeError(
                    f"COPY INTO loaded {loaded:,} of the {staged:,} staged rows of {sname}.{table}"
                )
            db.commit()
            return loaded
        except ShapeError:
            undo(db, sname, table, created)
            raise
        except Exception as exc:
            undo(db, sname, table, created)
            raise WriteError(f"loading {sname}.{table} failed: {_tsql.redact(str(exc))}") from exc
        finally:
            self._cleanup(folder)

    def _prepare(
        self,
        schema_name: str,
        table: str,
        mode: str,
        schema: pa.Schema,
        columns: Mapping[str, Mapping[str, Any]] | None,
        primary_key: Sequence[str],
    ) -> bool:
        """Make the table ready for ``mode`` (a subclass adds table options here)."""
        return prepare_table(
            self.db, schema_name, table, mode, schema, columns=columns, primary_key=primary_key
        )

    def _copy_statement(self, schema_name: str, table: str, folder: Any) -> str:
        return copy_into_sql(schema_name, table, folder.https() + "/")

    def _stage(
        self,
        table: str,
        folder: onelake.OneLakePath,
        first: pa.RecordBatch,
        rest: Iterator[pa.RecordBatch],
        chunk_rows: int,
    ) -> int:
        def normalized() -> Iterator[pa.RecordBatch]:
            yield _tsql.normalize_batch(first)
            for batch in rest:
                _tsql.check_columns(first.schema, batch, table)
                yield _tsql.normalize_batch(batch)

        schema = _tsql.normalize_schema(first.schema)
        staged = 0
        for index, group in enumerate(chunked(normalized(), chunk_rows)):
            path = folder.join(f"chunk_{index:06d}.parquet").abfss()

            def put(handle: BinaryIO, g: list[pa.RecordBatch] = group) -> None:
                write_batches(handle, "parquet", g, schema)

            self.storage.write(path, put)
            staged += sum(b.num_rows for b in group)
        return staged

    def _copy(
        self,
        schema_name: str,
        table: str,
        folder: onelake.OneLakePath,
        mode: str,
        created: bool,
        staged: int,
    ) -> int:
        db = self.db
        before = None
        if mode == "append" and not created:
            before = int(db.execute(_tsql.count_sql(schema_name, table)).fetchone()[0])
        cursor = db.execute(self._copy_statement(schema_name, table, folder))
        reported = getattr(cursor, "rowcount", -1)
        if isinstance(reported, int) and reported >= 0:
            return reported
        count = int(db.execute(_tsql.count_sql(schema_name, table)).fetchone()[0])
        return count - (before or 0)

    def _cleanup(self, folder: onelake.OneLakePath) -> None:
        try:
            self.storage.remove(folder.abfss(), recursive=True)
        except Exception as exc:  # the write's own outcome matters more than the leftovers
            warnings.warn(
                f"could not remove the staged files at {folder.abfss()}: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )

    def write_tables(
        self,
        tables: Mapping[str, Iterable[pa.RecordBatch]],
        *,
        order: Sequence[str] | None = None,
        options: Mapping[str, Mapping[str, Any]] | None = None,
        **common: Any,
    ) -> WriteResult:
        """Write tables in ``order`` (default: the mapping's order); see
        :meth:`SqlDatabaseWriter.write_tables`."""
        return write_many(self, tables, order, options, common)
