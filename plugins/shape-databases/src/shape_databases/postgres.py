"""PostgreSQL sink: ``postgresql://user@host:5432/database?sslmode=require``.

Rows are streamed with ``COPY "schema"."table" ("a", "b") FROM STDIN`` (psycopg 3, text
format; ``write_row`` converts each Python value, so values are COPY data and never part of a
statement). Batches are read one at a time and rows go straight to the server, so memory is
bounded by one batch however long the stream runs.

Transactions. By default the whole table is one transaction: the ``CREATE TABLE`` (PostgreSQL
DDL is transactional), a ``replace``'s drop and the ``COPY`` commit together or not at all, and
a failure leaves the database as it was. With ``commit_rows=N`` the write commits every N rows
(one ``COPY`` per N rows) so readers see rows while a stream runs; the table is committed before
the first rows. A failure then rolls back only the uncommitted rows; the committed ones stay and
:class:`WriteError` says how many (``rows_committed``).

Options: ``write_mode``, ``schema_name``, ``table_prefix``, ``batch_size`` (rows converted per
step), ``commit_rows``, ``columns`` / ``primary_key`` / ``schema`` (for the created table, as the
``sql`` sink), and the password sources of :mod:`shape_databases._auth`. ``connect`` takes a
function ``connect(**params) -> connection`` and ``connection`` an open connection (used as
is, never closed): both are for tests and hosts that manage their own connections.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError

from . import _sql
from ._base import DatabaseSink, Plan, as_int, as_text, chain_batches, chunks, one_of
from .errors import WriteError

SSL_MODES = ("disable", "allow", "prefer", "require", "verify-ca", "verify-full")


class PostgresSink(DatabaseSink):
    """``RecordBatch``es for one table to a PostgreSQL table with ``COPY``."""

    name = "postgres"
    schemes = ("postgresql", "postgres")
    dialect = "postgres"
    password_env = ("SHAPE_POSTGRES_PASSWORD", "PGPASSWORD")
    uri_params = {
        "sslmode": one_of(*SSL_MODES),
        "sslrootcert": as_text,
        "sslcert": as_text,
        "sslkey": as_text,
        "connect_timeout": as_int,
        "application_name": as_text,
    }
    exists_sql = (
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = COALESCE(%s::text, current_schema()::text) AND table_name = %s"
    )

    def connect_params(self, plan: Plan) -> dict[str, Any]:
        t = plan.target
        params: dict[str, Any] = {k: v for k, v in t.params.items()}
        for key, value in (("host", t.host), ("port", t.port), ("user", t.user)):
            if value is not None:
                params[key] = value
        if t.database:
            params["dbname"] = t.database
        params.setdefault("application_name", "shape")
        if plan.secret is not None:
            params["password"] = plan.secret.reveal()
        return params

    def default_connect(self, **params: Any) -> Any:
        try:
            psycopg = importlib.import_module("psycopg")
        except ImportError:
            raise ShapeError(
                "the postgres sink needs psycopg 3: pip install 'sqllocks-shape[postgres]'"
            ) from None
        return psycopg.connect(**params, autocommit=False)

    def ddl_is_transactional(self) -> bool:
        return True

    def load(
        self,
        conn: Any,
        qualified: str,
        plan: Plan,
        first: pa.RecordBatch | None,
        rest: Iterator[pa.RecordBatch],
        progress: list[int],
    ) -> int:
        if first is None:
            return 0
        names = first.schema.names
        statement = (
            f"COPY {qualified} ({', '.join(_sql.quote(n, self.dialect) for n in names)}) FROM STDIN"
        )
        converters = _sql.converters_for(first.schema, self.dialect)

        def rows() -> Iterator[tuple[Any, ...]]:
            for batch in chain_batches(first, rest):
                _sql.check_batch(first.schema, batch, plan.table)
                for start in range(0, batch.num_rows, plan.batch_size):
                    piece = batch.slice(start, plan.batch_size)
                    yield from _sql.row_iterator(piece, self.dialect, converters)

        total = 0
        cur = conn.cursor()
        try:
            for group in chunks(rows(), plan.commit_rows):
                counted = 0
                with cur.copy(statement) as copy:
                    for row in group:
                        copy.write_row(row)
                        counted += 1
                total += counted
                if plan.commit_rows:
                    conn.commit()
                    progress[0] = total
        finally:
            cur.close()
        return total


__all__ = ["SSL_MODES", "PostgresSink", "WriteError"]
