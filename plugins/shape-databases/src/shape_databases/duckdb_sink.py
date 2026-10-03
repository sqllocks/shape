"""The ``duckdb`` sink: tables into a local DuckDB database file, straight from Arrow.

    duckdb:///PATH.duckdb[?schema=main]

``duckdb:///data/out.duckdb`` is a path relative to the working directory and
``duckdb:////var/data/out.duckdb`` (four slashes) an absolute one, as in SQLAlchemy URLs; on
Windows ``duckdb:///C:/data/out.duckdb``. The database is created when it is missing. A URI with a
host, a password, no path or ``:memory:`` (the data would vanish with the connection) is refused.

Each Arrow batch is handed to DuckDB as an Arrow table (a zero-copy scan: no value is converted in
Python), then ``INSERT ... SELECT``-ed into the table. A missing table is created from the Arrow
schema:

=====================================  ====================================================
Arrow type                             DuckDB column
=====================================  ====================================================
bool                                   ``BOOLEAN``
int8 / 16 / 32 / 64                    ``TINYINT`` / ``SMALLINT`` / ``INTEGER`` / ``BIGINT``
uint8 / 16 / 32 / 64                   ``UTINYINT`` / ``USMALLINT`` / ``UINTEGER`` / ``UBIGINT``
float32 / float64 (float16 as float32) ``FLOAT`` / ``DOUBLE``
decimal128(p, s), p at most 38         ``DECIMAL(p,s)``
string, large_string, dictionary       ``VARCHAR``; ``UUID`` when the column's ``columns`` entry
                                       says ``"type": "uuid"``
binary, large_binary, fixed binary     ``BLOB``
date32 / date64                        ``DATE``
time32 / time64                        ``TIME`` (nanoseconds are cut to microseconds)
timestamp, no zone: s / ms / us / ns   ``TIMESTAMP_S`` / ``TIMESTAMP_MS`` / ``TIMESTAMP`` /
                                       ``TIMESTAMP_NS``
timestamp with a zone                  ``TIMESTAMPTZ`` (microseconds)
=====================================  ====================================================

Anything else (nested values, durations) is refused with the column named: convert it first. The
primary key (``primary_key``) is a ``PRIMARY KEY`` constraint; a column is ``NOT NULL`` when it is
a key column or its ``columns`` entry says ``"nullable": false``. Reading the table back gives
the same ``shape.repro.dataset_id`` as the tables that were written.

Write modes (``write_mode``, or ``?write_mode=`` in the URI; the option wins): ``create`` (the
default: an existing table is an error and is never touched), ``append`` (the table is created
when missing), ``truncate`` (``DELETE`` all rows, then add; created when missing), ``replace``
(drop and create again) and ``upsert`` (``INSERT OR REPLACE`` on the primary key, so rerunning the
same load leaves the same rows; a table without a primary key is refused:
``upsert needs a primary key on <table>``).

One table is one transaction: a failure rolls everything back, including the table this call
created and a ``replace``'s drop (a ``?schema=`` schema is created first, in a step of its own that
stays). With ``commit_rows=N`` (``--commit-rows``) the rows are committed
every ``N`` rows (rounded up to a batch), so another connection sees them as they arrive; a
failure then rolls back only the open chunk (``WriteError.rows_committed`` says how many stay).
Tables of one run are written at the same time (``shape generate --to`` in parallel, ``shape emit``
with every table open for the whole stream): each write has its own connection and transaction, and
DuckDB lets transactions on different tables of one file proceed side by side.

A database file that another process holds locked ends the write with exit code 2 and DuckDB's own
message. DuckDB is an optional dependency (``pip install 'sqllocks-shape-databases[duckdb]'``,
``'sqllocks-shape[duckdb]'``): it is imported when a write starts.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.types as pat  # type: ignore[import-untyped]

from shape.errors import ShapeError

from . import _sql
from ._base import as_int, as_text, one_of, parse_uri
from .errors import WriteError

log = logging.getLogger("shape_databases")

WRITE_MODES = (*_sql.WRITE_MODES, "upsert")
DEFAULT_SCHEMA = "main"
_VIEW = "shape_batch"
_SCHEMA_LOCK = threading.Lock()  # two transactions creating the same schema conflict in DuckDB
_INTS = {
    (True, 8): "TINYINT",
    (True, 16): "SMALLINT",
    (True, 32): "INTEGER",
    (True, 64): "BIGINT",
    (False, 8): "UTINYINT",
    (False, 16): "USMALLINT",
    (False, 32): "UINTEGER",
    (False, 64): "UBIGINT",
}
_TIMESTAMPS = {"s": "TIMESTAMP_S", "ms": "TIMESTAMP_MS", "us": "TIMESTAMP", "ns": "TIMESTAMP_NS"}


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _check_name(name: Any, kind: str) -> str:
    if not isinstance(name, str) or not name:
        raise ShapeError(f"{kind} name must be a non-empty string")
    if "\x00" in name:
        raise ShapeError(f"{kind} name {name!r} contains a NUL character")
    return name


def _qualified(schema_name: str, table: str) -> str:
    return f"{_quote(schema_name)}.{_quote(table)}"


def duckdb_type(field: pa.Field, meta: Mapping[str, Any]) -> str:
    """The DuckDB column type that holds Arrow ``field`` (see the module docstring)."""
    kind = field.type
    if pat.is_dictionary(kind):
        kind = kind.value_type
    if pat.is_boolean(kind):
        return "BOOLEAN"
    if pat.is_integer(kind):
        return _INTS[(bool(pat.is_signed_integer(kind)), kind.bit_width)]
    if pat.is_floating(kind):
        return "DOUBLE" if kind.bit_width == 64 else "FLOAT"
    if pat.is_decimal(kind):
        if kind.precision > 38:
            raise ShapeError(f"column {field.name!r}: DuckDB decimals hold at most 38 digits")
        return f"DECIMAL({kind.precision},{kind.scale})"
    if pat.is_timestamp(kind):
        return "TIMESTAMPTZ" if kind.tz is not None else _TIMESTAMPS[kind.unit]
    if pat.is_date(kind):
        return "DATE"
    if pat.is_time(kind):
        return "TIME"
    if pat.is_binary(kind) or pat.is_large_binary(kind) or pat.is_fixed_size_binary(kind):
        return "BLOB"
    if pat.is_string(kind) or pat.is_large_string(kind):
        return "UUID" if str(meta.get("type")) == "uuid" else "VARCHAR"
    raise ShapeError(
        f"column {field.name!r} has type {kind}, which a DuckDB column written by Shape cannot "
        "hold: convert it (for example to a JSON string) before writing"
    )


def create_table_sql(
    schema_name: str,
    table: str,
    schema: pa.Schema,
    *,
    columns: Mapping[str, Mapping[str, Any]] | None = None,
    primary_key: Sequence[str] = (),
) -> str:
    columns = columns or {}
    unknown = [c for c in primary_key if c not in schema.names]
    if unknown:
        raise ShapeError(f"primary key column {unknown[0]!r} is not a column of {table!r}")
    lines = []
    for field in schema:
        meta = columns.get(field.name, {})
        required = field.name in primary_key or not bool(meta.get("nullable", True))
        lines.append(
            f"  {_quote(_check_name(field.name, 'column'))} {duckdb_type(field, meta)}"
            f"{' NOT NULL' if required else ''}"
        )
    if primary_key:
        lines.append(f"  PRIMARY KEY ({', '.join(_quote(c) for c in primary_key)})")
    return f"CREATE TABLE {_qualified(schema_name, table)} (\n" + ",\n".join(lines) + "\n)"


def normalize(batch: pa.RecordBatch) -> pa.RecordBatch:
    """Dictionary columns decoded, nanosecond times cut to microseconds, half floats widened, and
    zoned timestamps in microseconds: the Arrow types DuckDB's columns above take."""
    arrays = []
    changed = False
    for column in batch.columns:
        kind = column.type
        if pat.is_dictionary(kind):
            column, kind = column.cast(kind.value_type), kind.value_type
            changed = True
        if pat.is_time(kind) and kind.bit_width == 64 and kind.unit == "ns":
            column, changed = column.cast(pa.time64("us"), safe=False), True
        elif pat.is_float16(kind):
            column, changed = column.cast(pa.float32()), True
        elif pat.is_timestamp(kind) and kind.tz is not None and kind.unit != "us":
            column, changed = column.cast(pa.timestamp("us", kind.tz), safe=False), True
        arrays.append(column)
    return pa.RecordBatch.from_arrays(arrays, names=batch.schema.names) if changed else batch


def _file_path(uri: str, allowed: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    target = parse_uri(uri, ("duckdb",), allowed)
    if target.host or target.port or target.user:
        raise ShapeError(
            "a DuckDB URI is a file path, duckdb:///PATH.duckdb (or duckdb:////absolute/path): "
            "it has no host, port or user"
        )
    path = target.database
    if not path:
        raise ShapeError("give the database file: duckdb:///PATH.duckdb")
    if path == ":memory:" or path.startswith(":memory:"):
        raise ShapeError(
            "duckdb:///:memory: would lose the data when the write ends: give a file, "
            "duckdb:///PATH.duckdb"
        )
    return path, target.params


def _driver() -> Any:
    try:
        import duckdb
    except ImportError as exc:
        raise ShapeError(
            "the duckdb sink needs DuckDB: pip install 'sqllocks-shape-databases[duckdb]' "
            "(or 'sqllocks-shape[duckdb]')"
        ) from exc
    return duckdb


class DuckDbSink:
    """Rows into a local DuckDB file; see the module docstring."""

    name = "duckdb"
    schemes = ("duckdb",)
    uri_params: Mapping[str, Any] = {
        "schema": as_text,
        "write_mode": one_of(*WRITE_MODES),
        "commit_rows": as_int,
    }

    def write(self, uri: str, table: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        path, query = _file_path(uri, self.uri_params)
        mode = options.get("write_mode", query.get("write_mode", "create"))
        if mode not in WRITE_MODES:
            raise ShapeError(f"unknown write mode {mode!r}; choose one of {', '.join(WRITE_MODES)}")
        schema_name = options.get("schema_name")
        schema_name = _check_name(
            query.get("schema", DEFAULT_SCHEMA) if schema_name is None else schema_name, "schema"
        )
        _check_name(table, "table")
        commit_rows = _sql.positive_int(
            options.get("commit_rows", query.get("commit_rows")), "commit_rows", optional=True
        )
        key = list(options.get("primary_key") or [])
        for name in key:
            _check_name(name, "column")
        if mode == "upsert" and not key:
            raise ShapeError(f"upsert needs a primary key on {table}")
        meta: Mapping[str, Mapping[str, Any]] = options.get("columns") or {}
        given = options.get("schema")
        if given is not None and not isinstance(given, pa.Schema):
            raise ShapeError("schema must be a pyarrow Schema")
        it = iter(batches)
        first = next(it, None)
        use_schema = first.schema if first is not None else given
        if use_schema is None:
            raise ShapeError(
                f"table {table!r} has no batches and no schema: pass schema= to create it empty"
            )
        duckdb = _driver()
        return self._write(
            duckdb, path, schema_name, table, mode, use_schema, first, it, meta, key, commit_rows
        )

    def _write(
        self,
        duckdb: Any,
        path: str,
        schema_name: str,
        table: str,
        mode: str,
        schema: pa.Schema,
        first: pa.RecordBatch | None,
        rest: Iterator[pa.RecordBatch],
        meta: Mapping[str, Mapping[str, Any]],
        key: list[str],
        commit_rows: int | None,
    ) -> int:
        try:
            con = duckdb.connect(path)
        except duckdb.Error as exc:
            raise ShapeError(f"cannot open the DuckDB database {path}: {exc}") from None
        committed = [0]
        failure: WriteError | None = None
        try:
            try:
                if schema_name != DEFAULT_SCHEMA:  # its own committed step, never inside a table's
                    with _SCHEMA_LOCK:
                        con.execute(f"CREATE SCHEMA IF NOT EXISTS {_quote(schema_name)}")
                con.begin()
                self._prepare(con, schema_name, table, mode, schema, meta, key)
                if commit_rows:
                    con.commit()  # the table is there for readers before the first rows
                    con.begin()
                rows = self._load(
                    con, schema_name, table, mode, first, rest, schema, commit_rows, committed
                )
                con.commit()
                log.debug("wrote %d rows to %s in %s", rows, table, path)
                return rows
            except ShapeError:
                self._rollback(con)
                raise
            except Exception as exc:
                self._rollback(con)
                kept = (
                    f"; {committed[0]} rows were committed before it failed" if committed[0] else ""
                )
                failure = WriteError(
                    f"writing {_qualified(schema_name, table)} in {path} failed "
                    f"({type(exc).__name__}): {exc}{kept}",
                    committed[0],
                )
        finally:
            con.close()
        raise failure

    @staticmethod
    def _rollback(con: Any) -> None:
        try:
            con.rollback()
        except Exception:  # noqa: S110  # nosec B110 - no transaction left to undo
            pass

    def _exists(self, con: Any, schema_name: str, table: str) -> bool:
        row = con.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
            [schema_name, table],
        ).fetchone()
        return row is not None

    def _prepare(
        self,
        con: Any,
        schema_name: str,
        table: str,
        mode: str,
        schema: pa.Schema,
        meta: Mapping[str, Mapping[str, Any]],
        key: Sequence[str],
    ) -> None:
        name = _qualified(schema_name, table)
        exists = self._exists(con, schema_name, table)
        if mode == "create" and exists:
            raise ShapeError(
                f"table {table} already exists; set write_mode to append, truncate, replace or "
                "upsert to write into it"
            )
        if mode == "replace" and exists:
            con.execute(f"DROP TABLE {name}")
            exists = False
        elif mode == "truncate" and exists:
            con.execute(f"DELETE FROM {name}")  # nosec B608 - quoted names only
        if not exists:
            con.execute(create_table_sql(schema_name, table, schema, columns=meta, primary_key=key))

    def _load(
        self,
        con: Any,
        schema_name: str,
        table: str,
        mode: str,
        first: pa.RecordBatch | None,
        rest: Iterator[pa.RecordBatch],
        schema: pa.Schema,
        commit_rows: int | None,
        committed: list[int],
    ) -> int:
        if first is None:
            return 0
        names = list(schema.names)
        columns = ", ".join(_quote(n) for n in names)
        verb = "INSERT OR REPLACE INTO" if mode == "upsert" else "INSERT INTO"
        sql = (  # every name is quoted; the rows come from the registered Arrow table
            f"{verb} {_qualified(schema_name, table)} ({columns}) "  # nosec B608
            f"SELECT {columns} FROM {_VIEW}"
        )
        rows = 0
        pending = 0

        def batches() -> Iterator[pa.RecordBatch]:
            yield first
            yield from rest

        for raw in batches():
            if list(raw.schema.names) != names:
                raise ShapeError(
                    f"a batch for {table!r} has columns {list(raw.schema.names)} but the first "
                    f"one had {names}"
                )
            if raw.num_rows == 0:
                continue
            con.register(_VIEW, pa.Table.from_batches([normalize(raw)]))
            try:
                con.execute(sql)
            finally:
                con.unregister(_VIEW)
            rows += raw.num_rows
            pending += raw.num_rows
            if commit_rows and pending >= commit_rows:
                con.commit()
                con.begin()
                pending = 0
                committed[0] = rows
        return rows
