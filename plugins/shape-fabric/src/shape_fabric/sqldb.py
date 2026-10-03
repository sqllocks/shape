"""SQL Database writer: tables into Fabric SQL database, Azure SQL, SQL Server or a Warehouse.

    writer = SqlDatabaseWriter(connection_string, credential=cred)
    writer.write_table("customer", batches, write_mode="create")

**Write modes** (``write_mode``), chosen by the caller; the default never touches data that is
already there:

``create``    create the table; an existing table is an error (the default)
``append``    add rows; the table is created when it is missing
``truncate``  empty the table, then add rows; created when missing
``replace``   drop the table and create it again (destroys the old rows)

Rows are sent as parameterised ``INSERT`` statements, ``batch_size`` rows per round trip (default
5,000). A table is written in one transaction by default: a failure rolls back its rows, and a
table this call created is dropped again. With ``commit_rows=N`` the writer instead commits after
every ``N`` rows (rounded up to a whole ``batch_size`` round trip) while it consumes the batches,
so a reader sees the rows as they arrive (streaming use); a failure then rolls back only the open
chunk, and the rows already committed, and the table, stay. (``replace`` has already dropped
the old table by then; ``create`` and the other modes lose nothing.) A failure stops the run
with :class:`WriteError`, whose ``result`` lists the tables that were completed.

Names are quoted and checked; values are never part of a statement. ``connection_string`` may be
the ODBC form or the ADO.NET form the Fabric portal shows. ``credential`` (see
:mod:`shape_fabric._auth`) signs in with Microsoft Entra; leave it out for a SQL login in the
connection string. ``connection`` takes an open DB-API connection (the Fabric User Data
Functions' own); it is used as is and never closed. A Warehouse (``*.datawarehouse.fabric.
microsoft.com``) is recognised and gets Warehouse types; for bulk loading it use
:class:`shape_fabric.warehouse.WarehouseWriter`.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError

from . import _tsql
from .errors import WriteError, WriteResult

WRITE_MODES = ("create", "append", "truncate", "replace")
DEFAULT_BATCH_SIZE = 5000
DEFAULT_SCHEMA = "dbo"


def check_mode(mode: str) -> str:
    if mode not in WRITE_MODES:
        raise ShapeError(f"unknown write mode {mode!r}; choose one of {', '.join(WRITE_MODES)}")
    return mode


class SqlConnection:
    """One lazily opened connection, plus the statements every SQL writer needs."""

    def __init__(
        self,
        connection_string: str | None,
        credential: Any,
        connection: Any,
        connect: Callable[..., Any] | None,
        warehouse: bool | None,
    ) -> None:
        if connection is None and not connection_string:
            raise ShapeError("give a connection_string (or an open connection)")
        self._injected = connection
        self._conn = connection
        self._connect = connect or _tsql.connect
        self._credential = credential
        self.connection_string = (
            _tsql.normalize_connection_string(connection_string) if connection_string else None
        )
        self.warehouse = (
            _tsql.is_warehouse(self.connection_string) if warehouse is None else warehouse
        )

    def get(self) -> Any:
        if self._conn is None:
            assert self.connection_string is not None
            self._conn = self._connect(self.connection_string, self._credential)
        return self._conn

    def close(self) -> None:
        if self._conn is not None and self._injected is None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    # statements -----------------------------------------------------------------------
    def execute(self, sql: str, *params: Any) -> Any:
        cursor = self.get().cursor()
        cursor.execute(sql, *params)
        return cursor

    def table_exists(self, schema_name: str, table: str) -> bool:
        cursor = self.execute(
            "SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?",
            schema_name,
            table,
        )
        return cursor.fetchone() is not None

    def ensure_schema(self, schema_name: str) -> None:
        if schema_name.lower() == "dbo":
            return
        self.execute(_tsql.create_schema_sql(schema_name), schema_name)
        self.get().commit()

    def commit(self) -> None:
        self.get().commit()

    def rollback(self) -> None:
        try:
            if self._conn is not None:
                self._conn.rollback()
        except Exception:  # noqa: S110  # nosec B110 - a dead connection has nothing to undo
            pass


def _peek(
    batches: Iterable[pa.RecordBatch],
) -> tuple[pa.RecordBatch | None, Iterator[pa.RecordBatch]]:
    it = iter(batches)
    return next(it, None), it


def prepare_table(
    db: SqlConnection,
    schema_name: str,
    table: str,
    mode: str,
    schema: pa.Schema,
    *,
    columns: Mapping[str, Mapping[str, Any]] | None = None,
    primary_key: Sequence[str] = (),
) -> bool:
    """Make the target ready for ``mode`` (see the module docstring); ``schema`` is normalized.
    Returns whether this call created the table."""
    db.ensure_schema(schema_name)
    exists = db.table_exists(schema_name, table)
    if mode == "create" and exists:
        raise ShapeError(
            f"table {schema_name}.{table} already exists; set write_mode to append, truncate "
            "or replace to write into it"
        )
    if mode == "replace" and exists:
        db.execute(_tsql.drop_table_sql(schema_name, table))
        db.commit()
        exists = False
    if exists and mode == "truncate":
        db.execute(_tsql.truncate_sql(schema_name, table))
        db.commit()
    if exists:
        return False
    db.execute(
        _tsql.create_table_sql(
            schema_name,
            table,
            schema,
            warehouse=db.warehouse,
            columns=columns,
            primary_key=primary_key,
        )
    )
    db.commit()
    return True


def write_many(
    writer: Any,
    tables: Mapping[str, Iterable[pa.RecordBatch]],
    order: Sequence[str] | None,
    options: Mapping[str, Mapping[str, Any]] | None,
    common: Mapping[str, Any],
) -> WriteResult:
    """Write each table with ``writer.write_table``, stopping at the first failure with a
    :class:`WriteError` whose ``result`` lists the tables completed before it."""
    start = time.monotonic()
    result = WriteResult(writer.destination)
    names = list(order or tables)
    missing = [t for t in names if t not in tables]
    if missing:  # before anything is written
        raise ShapeError(f"no data was given for table {missing[0]!r}")
    for table in names:
        per = {**common, **dict((options or {}).get(table, {}))}
        try:
            result.per_table[table] = writer.write_table(table, tables[table], **per)
        except WriteError as exc:
            exc.result = result
            result.elapsed_seconds = time.monotonic() - start
            raise
        except ShapeError as exc:
            result.elapsed_seconds = time.monotonic() - start
            raise WriteError(str(exc), result) from exc
    result.elapsed_seconds = time.monotonic() - start
    return result


def undo(db: SqlConnection, schema_name: str, table: str, created: bool) -> None:
    """After a failed write: roll back the open transaction and drop a table this call made."""
    db.rollback()
    if created:
        try:
            db.execute(_tsql.drop_table_sql(schema_name, table, if_exists=True))
            db.commit()
        except Exception:  # noqa: S110  # nosec B110 - the original error is the one to report
            pass


class SqlDatabaseWriter:
    """Tables to a SQL database; see the module docstring."""

    def __init__(
        self,
        connection_string: str | None = None,
        *,
        credential: Any = None,
        connection: Any = None,
        connect: Callable[..., Any] | None = None,
        warehouse: bool | None = None,
        schema_name: str = DEFAULT_SCHEMA,
    ) -> None:
        self.db = SqlConnection(connection_string, credential, connection, connect, warehouse)
        self.schema_name = schema_name

    @property
    def destination(self) -> str:
        return _tsql.redact(self.db.connection_string or "<open connection>")

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> SqlDatabaseWriter:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ ddl
    def create_ddl(
        self,
        table: str,
        schema: pa.Schema,
        *,
        schema_name: str | None = None,
        columns: Mapping[str, Mapping[str, Any]] | None = None,
        primary_key: Sequence[str] = (),
    ) -> str:
        """The ``CREATE TABLE`` statement for ``schema``, without running it."""
        return _tsql.create_table_sql(
            schema_name or self.schema_name,
            table,
            _tsql.normalize_schema(schema),
            warehouse=self.db.warehouse,
            columns=columns,
            primary_key=primary_key,
        )

    # ---------------------------------------------------------------- write
    def write_table(
        self,
        table: str,
        batches: Iterable[pa.RecordBatch],
        *,
        write_mode: str = "create",
        batch_size: int = DEFAULT_BATCH_SIZE,
        commit_rows: int | None = None,
        schema_name: str | None = None,
        columns: Mapping[str, Mapping[str, Any]] | None = None,
        primary_key: Sequence[str] = (),
        schema: pa.Schema | None = None,
    ) -> int:
        """Write one table; return its row count. ``schema`` is needed only to create an
        empty table from no batches."""
        mode = check_mode(write_mode)
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
            raise ShapeError("batch_size must be a positive integer")
        if commit_rows is not None and (
            isinstance(commit_rows, bool) or not isinstance(commit_rows, int) or commit_rows < 1
        ):
            raise ShapeError("commit_rows must be a positive integer")
        sname = schema_name or self.schema_name
        _tsql.qualified(sname, table)  # validates both names before anything runs
        first, rest = _peek(batches)
        use_schema = first.schema if first is not None else schema
        if use_schema is None:
            raise ShapeError(
                f"table {table!r} has no batches and no schema: pass schema= to create it empty"
            )
        use_schema = _tsql.normalize_schema(use_schema)
        created = False
        db = self.db
        committed = [0]  # rows already committed by this call (commit_rows only)
        try:
            created = prepare_table(
                db, sname, table, mode, use_schema, columns=columns, primary_key=primary_key
            )
            rows = self._insert(sname, table, first, rest, batch_size, commit_rows, committed)
            db.commit()
            return rows
        except ShapeError:
            undo(db, sname, table, created and not committed[0])
            raise
        except Exception as exc:
            undo(db, sname, table, created and not committed[0])
            raise WriteError(f"writing {sname}.{table} failed: {_tsql.redact(str(exc))}") from exc

    def _insert(
        self,
        schema_name: str,
        table: str,
        first: pa.RecordBatch | None,
        rest: Iterator[pa.RecordBatch],
        batch_size: int,
        commit_rows: int | None = None,
        committed: list[int] | None = None,
    ) -> int:
        if first is None:
            return 0
        cursor = self.db.get().cursor()
        sql = _tsql.insert_sql(schema_name, table, first.schema.names)
        rows = 0
        pending = 0

        def batches() -> Iterator[pa.RecordBatch]:
            yield first
            yield from rest

        for raw in batches():
            _tsql.check_columns(first.schema, raw, table)
            batch = _tsql.normalize_batch(raw)
            for start in range(0, batch.num_rows, batch_size):
                piece = batch.slice(start, batch_size)
                if piece.num_rows == 0:
                    continue
                fast = _tsql.widest_first(piece)
                if hasattr(cursor, "fast_executemany"):
                    cursor.fast_executemany = fast
                cursor.executemany(sql, _tsql.rows_as_params(piece))
                rows += piece.num_rows
                pending += piece.num_rows
                if commit_rows is not None and pending >= commit_rows:
                    self.db.commit()
                    pending = 0
                    if committed is not None:
                        committed[0] = rows
        return rows

    def write_tables(
        self,
        tables: Mapping[str, Iterable[pa.RecordBatch]],
        *,
        order: Sequence[str] | None = None,
        options: Mapping[str, Mapping[str, Any]] | None = None,
        **common: Any,
    ) -> WriteResult:
        """Write tables in ``order`` (default: the mapping's order, parents first).

        ``common`` options (``write_mode``, ``batch_size``, ``schema_name``) apply to every
        table; ``options[table]`` adds per-table ones (``columns``, ``primary_key``).
        """
        return write_many(self, tables, order, options, common)
