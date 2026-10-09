"""SQL Database writer: tables into Fabric SQL database, Azure SQL, SQL Server or a Warehouse.

    writer = SqlDatabaseWriter(connection_string, credential=cred)
    writer.write_table("customer", batches, write_mode="create")

**Write modes** (``write_mode``), chosen by the caller; the default never touches data that is
already there:

``create``    create the table; an existing table is an error (the default)
``append``    add rows; the table is created when it is missing
``truncate``  empty the table, then add rows; created when missing
``replace``   drop the table and create it again; the old rows are gone once the new ones commit
``upsert``    add the rows that are missing and update the others, matched on the primary key
              (``MERGE``); the table is created when it is missing; a table without a primary key
              is refused. Rerunning the same load leaves the same rows, and finishes a load that
              was killed half way

``truncate`` on a table that a foreign key references uses ``DELETE`` (SQL Server refuses
``TRUNCATE`` there); the delete fails while rows of a child table still point at it.

**Identity.** A column whose ``columns`` entry has ``"identity": {"start": N, "step": M}`` (or
``True``) is created as ``BIGINT IDENTITY(N, M)``. ``identity="keep"`` (the default) inserts the
generated values between ``SET IDENTITY_INSERT ... ON`` and ``OFF``, so the keys that child foreign
keys reference are kept; ``identity="server"`` leaves the column out and lets the server number the
rows, and is refused when ``identity_references`` (``{"child", "column", "parent_column"}``) says a
foreign key points at the column. A Warehouse has no identity: the setting is ignored there.

**Constraints.** ``constraints="disable"`` runs ``ALTER TABLE ... NOCHECK CONSTRAINT ALL`` on a
table that already exists, loads, then ``ALTER TABLE ... WITH CHECK CHECK CONSTRAINT ALL``. When
that fails the rows stay, and :class:`~shape_fabric.errors.ConstraintError` (exit code 1) names each
constraint that does not hold; those are left disabled. A failed load puts checking back on.

Rows are sent as parameterised ``INSERT`` statements, ``batch_size`` rows per round trip (default
5,000). A table is written in one transaction by default, together with a ``truncate``'s
``TRUNCATE`` and a ``replace``'s ``DROP TABLE`` and ``CREATE TABLE``: a failure rolls back its
rows, an existing table keeps its old rows (``truncate`` and ``replace`` included), and a table
this call created is dropped again. With ``commit_rows=N`` the writer instead commits after
every ``N`` rows (rounded up to a whole ``batch_size`` round trip) while it consumes the batches,
so a reader sees the rows as they arrive (streaming use); a failure then rolls back only the open
chunk, and the rows already committed, and the table, stay. (A ``truncate`` or ``replace``
commits with the first chunk; a failure before it keeps the old rows.) A failure stops the run
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
from dataclasses import dataclass
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError

from . import _tsql
from .errors import ConstraintError, WriteError, WriteResult

WRITE_MODES = ("create", "append", "truncate", "replace")
SQL_WRITE_MODES = (*WRITE_MODES, "upsert")  # the SQL database writer; the others refuse upsert
IDENTITY_MODES = ("keep", "server")
CONSTRAINT_MODES = ("keep", "disable")
DEFAULT_BATCH_SIZE = 5000
DEFAULT_SCHEMA = "dbo"


def check_mode(mode: str) -> str:
    if mode not in WRITE_MODES:
        raise ShapeError(f"unknown write mode {mode!r}; choose one of {', '.join(WRITE_MODES)}")
    return mode


def check_sql_mode(mode: str) -> str:
    if mode not in SQL_WRITE_MODES:
        raise ShapeError(f"unknown write mode {mode!r}; choose one of {', '.join(SQL_WRITE_MODES)}")
    return mode


def _choice(name: str, value: Any, choices: Sequence[str]) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ShapeError(f"{name} must be {' or '.join(choices)}")
    return value


@dataclass(frozen=True)
class WritePlan:
    """The checked options of one table's write: nothing is connected before this exists."""

    mode: str
    identity: str
    constraints: str
    identity_columns: tuple[str, ...]
    primary_key: tuple[str, ...]


def plan_write(
    table: str,
    *,
    mode: str = "create",
    identity: Any = "keep",
    constraints: Any = "keep",
    columns: Mapping[str, Mapping[str, Any]] | None = None,
    primary_key: Sequence[str] = (),
    identity_references: Sequence[Mapping[str, str]] = (),
    warehouse: bool = False,
) -> WritePlan:
    """Check the write options of ``table`` and refuse an impossible combination: an unknown value,
    ``upsert`` without a primary key or on a Warehouse, ``identity=server`` where a foreign key
    references the identity column or the identity column is what an upsert matches on."""
    check_sql_mode(mode)
    identity = _choice("identity", identity, IDENTITY_MODES)
    constraints = _choice("constraints", constraints, CONSTRAINT_MODES)
    identity_columns = (
        ()
        if warehouse
        else tuple(name for name, meta in (columns or {}).items() if _tsql.identity_parts(meta))
    )
    if identity == "server":
        for ref in identity_references:
            if ref["parent_column"] in identity_columns:
                raise ShapeError(
                    f"identity=server would break the foreign key {ref['child']}.{ref['column']} "
                    f"-> {table}.{ref['parent_column']}"
                )
    if mode == "upsert":
        if warehouse:
            raise ShapeError("upsert is not supported for a Warehouse")
        if not primary_key:
            raise ShapeError(f"upsert needs a primary key on {table}")
        if identity == "server":
            clash = next((k for k in primary_key if k in identity_columns), None)
            if clash is not None:
                raise ShapeError(
                    f"identity=server cannot match rows on the identity key {table}.{clash}: "
                    "use identity=keep for an upsert"
                )
    return WritePlan(mode, identity, constraints, identity_columns, tuple(primary_key))


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
    options: str | None = None,
    synapse: bool = False,
) -> bool:
    """Make the target ready for ``mode`` (see the module docstring); ``schema`` is normalized.

    A ``truncate`` or ``replace`` of an existing table is left uncommitted: it commits with the
    rows, and a rollback brings the old rows back (#429). A table that did not exist is created
    and committed on its own. Returns whether this call committed a new table, which
    :func:`undo` drops again after a failure."""
    db.ensure_schema(schema_name)
    exists = db.table_exists(schema_name, table)
    if mode == "create" and exists:
        raise WriteError(
            f"table {schema_name}.{table} already exists; set write_mode to append, truncate "
            "or replace to write into it"
        )
    if exists and mode == "truncate":
        if (
            not db.warehouse
            and db.execute(_tsql.REFERENCED_SQL, _tsql.qualified(schema_name, table)).fetchone()
        ):
            db.execute(
                _tsql.delete_sql(schema_name, table)
            )  # TRUNCATE is refused on a target of a key
        else:
            db.execute(_tsql.truncate_sql(schema_name, table))
    if exists and mode != "replace":
        return False
    if exists:
        db.execute(_tsql.drop_table_sql(schema_name, table))
    db.execute(
        _tsql.create_table_sql(
            schema_name,
            table,
            schema,
            warehouse=db.warehouse,
            columns=columns,
            primary_key=primary_key,
            options=options,
            synapse=synapse,
        )
    )
    if exists:
        return False  # the drop and the new table commit with the rows, or roll back together
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
            schema,
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
        identity: str = "keep",
        constraints: str = "keep",
        identity_references: Sequence[Mapping[str, str]] = (),
    ) -> int:
        """Write one table; return its row count. ``schema`` is needed only to create an
        empty table from no batches."""
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
            raise ShapeError("batch_size must be a positive integer")
        if commit_rows is not None and (
            isinstance(commit_rows, bool) or not isinstance(commit_rows, int) or commit_rows < 1
        ):
            raise ShapeError("commit_rows must be a positive integer")
        plan = plan_write(
            table,
            mode=write_mode,
            identity=identity,
            constraints=constraints,
            columns=columns,
            primary_key=primary_key,
            identity_references=identity_references,
            warehouse=self.db.warehouse,
        )
        sname = schema_name or self.schema_name
        _tsql.qualified(sname, table)  # validates both names before anything runs
        first, rest = _peek(batches)
        use_schema = first.schema if first is not None else schema
        if use_schema is None:
            raise ShapeError(
                f"table {table!r} has no batches and no schema: pass schema= to create it empty"
            )
        unknown = [k for k in plan.primary_key if k not in use_schema.names]
        if unknown and plan.mode == "upsert":
            raise ShapeError(f"primary key column {unknown[0]!r} is not a column of {table!r}")
        if plan.identity == "server" and all(n in plan.identity_columns for n in use_schema.names):
            raise ShapeError(
                f"table {table!r} has only identity columns: identity=server has no column to "
                "insert; use identity=keep"
            )
        created = False
        disabled = False
        db = self.db
        committed = [0]  # rows already committed by this call (commit_rows only)
        try:
            created = prepare_table(
                db,
                sname,
                table,
                plan.mode,
                use_schema,
                columns=columns,
                primary_key=primary_key,
            )
            # a replaced table is new, like one this call created: nothing to disable; a
            # truncate's TRUNCATE (or DELETE) commits with the rows, so NOCHECK does too (#429)
            if plan.constraints == "disable" and not created and plan.mode != "replace":
                db.execute(_tsql.nocheck_sql(sname, table))
                if plan.mode != "truncate":
                    db.commit()
                disabled = True
            rows = self._load(
                sname, table, first, rest, plan, columns, batch_size, commit_rows, committed
            )
            db.commit()
        except Exception as exc:
            undo(db, sname, table, created and not committed[0])
            if disabled:
                self._enable_again(sname, table)
            if isinstance(exc, ShapeError):
                raise
            raise WriteError(f"writing {sname}.{table} failed: {_tsql.redact(str(exc))}") from exc
        if disabled:
            self._validate(sname, table)
        return rows

    def _enable_again(self, schema_name: str, table: str) -> None:
        """After a failed load: put constraint checking back on (without validating old rows)."""
        try:
            self.db.execute(_tsql.enable_sql(schema_name, table))
            self.db.commit()
        except Exception:  # noqa: S110  # nosec B110 - the load's own error is the one to report
            pass

    def _validate(self, schema_name: str, table: str) -> None:
        """After a load with constraints disabled: enable them and validate every row, or raise
        :class:`ConstraintError` naming each constraint that does not hold (left disabled)."""
        db = self.db
        where = f"{schema_name}.{table}"
        try:
            db.execute(_tsql.check_all_sql(schema_name, table))
            db.commit()
            return
        except Exception as exc:
            db.rollback()
            first_error = _tsql.redact(str(exc))
        qualified = _tsql.qualified(schema_name, table)
        failing: list[str] = []
        try:
            names = [
                row[0]
                for row in db.execute(_tsql.CONSTRAINT_NAMES_SQL, qualified, qualified).fetchall()
            ]
        except Exception:
            db.rollback()
            names = []
        for name in names:
            try:
                db.execute(_tsql.check_one_sql(schema_name, table, name))
                db.commit()
            except Exception:
                db.rollback()
                failing.append(f"{where}.{name}")
        listed = ", ".join(failing) if failing else f"{where} (see: {first_error})"
        raise ConstraintError(
            f"after loading {where}, these constraints do not hold and were left disabled: "
            f"{listed}; fix the rows, then run: ALTER TABLE {qualified} WITH CHECK CHECK "
            "CONSTRAINT ALL"
        )

    def _load(
        self,
        schema_name: str,
        table: str,
        first: pa.RecordBatch | None,
        rest: Iterator[pa.RecordBatch],
        plan: WritePlan,
        columns: Mapping[str, Mapping[str, Any]] | None,
        batch_size: int,
        commit_rows: int | None,
        committed: list[int],
    ) -> int:
        if first is None:
            return 0
        keep = plan.identity == "keep" and any(
            n in plan.identity_columns for n in first.schema.names
        )
        left_out = set(plan.identity_columns) if plan.identity == "server" else set()
        names = [n for n in first.schema.names if n not in left_out]
        if keep:
            self.db.execute(_tsql.identity_insert_sql(schema_name, table, True))
        try:
            if plan.mode == "upsert":
                return self._upsert(
                    schema_name, table, first, rest, plan, columns, names, batch_size, commit_rows,
                    committed,
                )  # fmt: skip
            return self._insert(
                schema_name, table, first, rest, batch_size, commit_rows, committed, names
            )
        finally:
            if keep:
                try:
                    self.db.execute(_tsql.identity_insert_sql(schema_name, table, False))
                except Exception:  # noqa: S110  # nosec B110 - a dead connection has nothing to undo
                    pass

    def _pieces(
        self,
        first: pa.RecordBatch,
        rest: Iterator[pa.RecordBatch],
        names: list[str],
        table: str,
        batch_size: int,
    ) -> Iterator[pa.RecordBatch]:
        """The rows as pieces of at most ``batch_size``, normalized, with only ``names``."""

        def batches() -> Iterator[pa.RecordBatch]:
            yield first
            yield from rest

        for raw in batches():
            _tsql.check_columns(first.schema, raw, table)
            batch = _tsql.normalize_batch(raw, warehouse=self.db.warehouse)
            if len(names) != batch.num_columns:
                batch = batch.select(names)
            for start in range(0, batch.num_rows, batch_size):
                piece = batch.slice(start, batch_size)
                if piece.num_rows:
                    yield piece

    def _send(self, cursor: Any, sql: str, piece: pa.RecordBatch) -> None:
        if hasattr(cursor, "fast_executemany"):
            cursor.fast_executemany = _tsql.widest_first(piece)
        cursor.executemany(sql, _tsql.rows_as_params(piece))

    def _insert(
        self,
        schema_name: str,
        table: str,
        first: pa.RecordBatch | None,
        rest: Iterator[pa.RecordBatch],
        batch_size: int,
        commit_rows: int | None = None,
        committed: list[int] | None = None,
        names: list[str] | None = None,
    ) -> int:
        if first is None:
            return 0
        names = names if names is not None else list(first.schema.names)
        cursor = self.db.get().cursor()
        sql = _tsql.insert_sql(schema_name, table, names)
        rows = 0
        pending = 0
        for piece in self._pieces(first, rest, names, table, batch_size):
            self._send(cursor, sql, piece)
            rows += piece.num_rows
            pending += piece.num_rows
            if commit_rows is not None and pending >= commit_rows:
                self.db.commit()
                pending = 0
                if committed is not None:
                    committed[0] = rows
        return rows

    def _upsert(
        self,
        schema_name: str,
        table: str,
        first: pa.RecordBatch,
        rest: Iterator[pa.RecordBatch],
        plan: WritePlan,
        columns: Mapping[str, Mapping[str, Any]] | None,
        names: list[str],
        batch_size: int,
        commit_rows: int | None,
        committed: list[int],
    ) -> int:
        """Each piece goes into the session temporary table and is merged into the target on the
        primary key: non-key columns are updated (an identity column never is), the rest are
        inserted."""
        db = self.db
        key = list(plan.primary_key)
        updates = [n for n in names if n not in key and n not in plan.identity_columns]
        stage_schema = pa.schema([first.schema.field(n) for n in names])
        db.execute(_tsql.drop_stage_sql())
        db.execute(_tsql.create_stage_sql(stage_schema, columns=columns, key=key))
        stage_sql = _tsql.stage_insert_sql(names)
        merge = _tsql.merge_sql(schema_name, table, names, key, updates)
        cursor = db.get().cursor()
        rows = 0
        pending = 0
        try:
            for piece in self._pieces(first, rest, names, table, batch_size):
                db.execute(_tsql.truncate_stage_sql())
                self._send(cursor, stage_sql, piece)
                db.execute(merge)
                rows += piece.num_rows
                pending += piece.num_rows
                if commit_rows is not None and pending >= commit_rows:
                    db.commit()
                    pending = 0
                    committed[0] = rows
        finally:
            try:
                db.execute(_tsql.drop_stage_sql())
            except Exception:  # noqa: S110  # nosec B110 - the session ends with the connection
                pass
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
