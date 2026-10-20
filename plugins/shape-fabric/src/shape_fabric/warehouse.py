"""Warehouse bulk writer: stage Parquet in OneLake, then ``COPY INTO``.

    writer = WarehouseWriter(
        connection_string,
        staging_path="onelake://<workspace-guid>/<lakehouse-guid>/Files",
        credential=cred,
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

``staging_path`` is the ``Files`` folder of a lakehouse, or a folder in it
(``abfss://`` or ``onelake://``). COPY uses workspace and item GUIDs; cross-workspace
OneLake sources must be in the same tenant. ``credential`` signs in both to the Warehouse
and to OneLake. That client credential does not control the Warehouse's server-side
OneLake token exchange. If the server rejects that exchange with SQL error 13840,
the writer rolls back the failed statement, reapplies any uncommitted truncate or
replace, and loads the staged Parquet through parameterized SQL inserts in batches
of 5,000 rows. It warns about this slower path, verifies the resulting row count,
and retains the same transaction, key and staging-cleanup behavior. Other COPY
errors remain errors; the writer never silently changes the server's identity.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import sys
import time
import uuid
import warnings
import zlib
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from typing import Any, BinaryIO, Self

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from shape.errors import ShapeError
from shape.generation.write_constraints import _PREPARED_CONSTRAINTS

from . import _tsql, onelake
from ._storage import Storage
from .errors import WriteError, WriteResult
from .lakehouse import write_batches
from .sqldb import SqlConnection, check_mode, constraint_plan, prepare_table, undo, write_many

DEFAULT_CHUNK_ROWS = 1_000_000
log = logging.getLogger(__name__)
#: the most seconds one table may spend in the INSERT fallback (``SHAPE_WAREHOUSE_INSERT_SECONDS``
#: overrides it): row-by-row loading into a Warehouse is far slower than COPY INTO, so a large
#: table fails here with the cause instead of running for hours
INSERT_FALLBACK_SECONDS = 600
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
        #: one line per table loaded by the INSERT fallback; also in ``WriteResult.notes``
        self.notes: list[str] = []

    def drain_notes(self) -> list[str]:
        """The fallback notes since the last call (``write_many`` puts them in the result)."""
        notes, self.notes = self.notes, []
        return notes

    @property
    def destination(self) -> str:
        return _tsql.redact(self.db.connection_string or "<open connection>")

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> Self:
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
        foreign_keys: list[dict[str, Any]] | None = None,
        unique: list[list[str]] | None = None,
        checks: list[dict[str, Any]] | None = None,
        defaults: Mapping[str, Any] | None = None,
        prepared_constraints: Any = None,
    ) -> int:
        """Write one table; return the rows loaded."""
        if prepared_constraints is not None and prepared_constraints is not _PREPARED_CONSTRAINTS:
            raise ShapeError("prepared_constraints is an internal preparation marker")
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
        use_schema = _tsql.normalize_schema(use_schema)
        declarations = {
            key: value
            for key, value in (
                ("foreign_keys", foreign_keys),
                ("unique", unique),
                ("checks", checks),
                ("defaults", defaults),
            )
            if value is not None
        }
        compiled = constraint_plan(sname, table, use_schema, True, declarations)
        _tsql.create_table_sql(
            sname,
            table,
            use_schema,
            warehouse=True,
            columns=columns,
            primary_key=primary_key,
            constraint_ddl=compiled,
        )
        for note in compiled.notes:
            log.warning("%s", note)
        folder = self._folder(table)
        created = False
        db = self.db
        try:
            if declarations or prepared_constraints is _PREPARED_CONSTRAINTS:
                created = prepare_table(
                    db,
                    sname,
                    table,
                    mode,
                    use_schema,
                    columns=columns,
                    primary_key=primary_key,
                    constraint_ddl=compiled,
                    prepared_constraints=prepared_constraints,
                )
            else:
                created = self._prepare(sname, table, mode, use_schema, columns, primary_key)
            if first is None:
                return 0
            staged = self._stage(table, folder, first, stream, chunk_rows)
            copy_mode = "append" if prepared_constraints is _PREPARED_CONSTRAINTS else mode
            try:
                loaded = self._copy(sname, table, folder, copy_mode, created, staged)
            except Exception as exc:
                # 13840 is a server-side OneLake token exchange failure, not a bad row.
                # The client's storage token cannot be supplied to COPY INTO. Do not
                # switch identities or replay any other COPY error automatically.
                if (
                    not isinstance(folder, onelake.OneLakePath)
                    or not re.search(r"\b13840\b", str(exc))
                    or "access token couldn't be fetched for storage path" not in str(exc).lower()
                ):
                    raise
                note = (
                    f"{sname}.{table}: COPY INTO failed with SQL error 13840 "
                    f"({_tsql.redact(str(exc))[:300]}); loading {staged:,} rows with "
                    "parameterized INSERT in 5,000-row batches instead, which is much slower "
                    "than COPY INTO (see docs/KNOWN_LIMITATIONS.md, Fabric Warehouse COPY INTO)"
                )
                self.notes.append(note)
                print(f"WARNING: {note}", file=sys.stderr, flush=True)
                warnings.warn(note, RuntimeWarning, stacklevel=2)
                db.rollback()
                # Reapply an uncommitted truncate/replace after rollback. A table this
                # call created was committed by prepare_table and is still present.
                self._prepare(
                    sname,
                    table,
                    "append" if created else copy_mode,
                    use_schema,
                    columns,
                    primary_key,
                )
                loaded = self._insert_staged(sname, table, folder)
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

    int96_timestamps = False  # staged Parquet timestamps as INT96 (a Synapse dedicated pool)

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
                write_batches(handle, "parquet", g, schema, int96_timestamps=self.int96_timestamps)

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

    def _insert_staged(self, schema_name: str, table: str, folder: onelake.OneLakePath) -> int:
        """Recover server token failure through the existing authenticated SQL connection.

        Read staged Parquet a batch at a time. Neither the whole table nor a whole
        staging chunk is materialized, and every file/Parquet/SQL handle is closed.
        The caller verifies the row count and commits or undoes the entire write.
        """
        count_sql = _tsql.count_sql(schema_name, table)
        raw = os.environ.get("SHAPE_WAREHOUSE_INSERT_SECONDS", "")
        limit = int(raw) if raw.strip().isdigit() else INSERT_FALLBACK_SECONDS
        deadline = time.monotonic() + limit
        cursor = self.db.get().cursor()
        total = 0
        try:
            before = int(cursor.execute(count_sql).fetchone()[0])
            index = 0
            while True:
                path = folder.join(f"chunk_{index:06d}.parquet").abfss()
                if not self.storage.exists(path):
                    break
                fs, fs_path = self.storage._remote(path)  # noqa: SLF001
                with fs.open(fs_path, "rb") as handle:
                    parquet = pq.ParquetFile(handle)
                    try:
                        for batch in parquet.iter_batches(batch_size=5000):
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise ShapeError(
                                    f"the parameterized INSERT fallback for {schema_name}.{table} "
                                    f"stopped after {limit} s with {total:,} rows sent: COPY INTO "
                                    "failed with SQL error 13840 and row-by-row loading is too "
                                    "slow for this table. Fix the OneLake credential COPY INTO "
                                    "uses (docs/KNOWN_LIMITATIONS.md); the write was rolled back"
                                )
                            with contextlib.suppress(AttributeError):
                                cursor.timeout = max(1, int(remaining))
                            sql = _tsql.insert_sql(schema_name, table, batch.schema.names)
                            _tsql.use_fast_executemany(cursor, batch)
                            cursor.executemany(sql, _tsql.rows_as_params(batch))
                            total += batch.num_rows
                    finally:
                        parquet.close()
                index += 1
            after = int(cursor.execute(count_sql).fetchone()[0])
            loaded = after - before
            if loaded != total:
                raise ShapeError(
                    f"parameterized INSERT loaded {loaded:,} of the {total:,} sent rows "
                    f"of {schema_name}.{table}"
                )
            return loaded
        finally:
            close = getattr(cursor, "close", None)
            if close is not None:
                close()

    def _cleanup(self, folder: onelake.OneLakePath) -> None:
        try:
            self.storage.remove(folder.abfss(), recursive=True)
            # OneLake keeps the emptied run folder and the shared ``staging`` folder above it:
            # take them away too once nothing is in them (another run's files keep them)
            run_folder = folder.abfss().rsplit("/", 1)[0]
            self.storage.remove_if_empty(run_folder)
            self.storage.remove_if_empty(run_folder.rsplit("/", 1)[0])
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
