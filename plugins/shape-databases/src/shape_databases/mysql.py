"""MySQL sink: ``mysql://user@host:3306/database?ssl_ca=/path/ca.pem``.

Client: PyMySQL (pure Python; no ``libmysqlclient`` to install). Rows are sent with
``executemany("INSERT INTO ... VALUES (%s, ...)")``, ``batch_size`` rows per call, which PyMySQL
turns into multi-row ``INSERT`` statements with every value bound as a parameter. ``LOAD DATA
LOCAL INFILE`` is not used: servers commonly disable it (``local_infile=0``) and enabling it on
the client opens the connection to a server-driven file read, so it is neither the default nor
an option.

Transactions. MySQL commits implicitly at every DDL statement, so unlike PostgreSQL the table
creation cannot be rolled back with the rows. By default all rows of the table are one
transaction (committed at the end); on a failure the rows roll back and a table this call
created is dropped again. ``replace`` has already dropped the old table by then, and ``truncate``
(also DDL in MySQL) has already emptied it. With ``commit_rows=N`` the write commits every N rows
so readers see rows while a stream runs; a failure then keeps the committed rows and the table,
and :class:`WriteError` says how many (``rows_committed``).

``schema_name`` is the MySQL database to write into (default: the one in the URI). NaN and
infinity, which MySQL cannot store, are written as NULL, as the ``sql`` sink's script does.
Options are those of the PostgreSQL sink (see :mod:`shape_databases.postgres`).
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError

from . import _sql
from ._base import DatabaseSink, Plan, as_bool, as_int, as_text, chain_batches, chunks

DEFAULT_CHARSET = "utf8mb4"


class MySqlSink(DatabaseSink):
    """``RecordBatch``es for one table to a MySQL table with batched multi-row ``INSERT``."""

    name = "mysql"
    schemes = ("mysql",)
    dialect = "mysql"
    password_env = ("SHAPE_MYSQL_PASSWORD", "MYSQL_PWD")
    uri_params = {
        "ssl_ca": as_text,
        "ssl_cert": as_text,
        "ssl_key": as_text,
        "ssl_verify_cert": as_bool,
        "ssl_verify_identity": as_bool,
        "charset": as_text,
        "connect_timeout": as_int,
    }
    exists_sql = (
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = COALESCE(%s, DATABASE()) AND table_name = %s"
    )

    def create_schema_sql(self, schema_name: str) -> str:
        # In MySQL a schema is a database (created, like a PostgreSQL schema, when missing).
        return f"CREATE DATABASE IF NOT EXISTS {_sql.quote(schema_name, self.dialect)}"

    def connect_params(self, plan: Plan) -> dict[str, Any]:
        t = plan.target
        params: dict[str, Any] = dict(t.params)
        for key, value in (("host", t.host), ("port", t.port), ("user", t.user)):
            if value is not None:
                params[key] = value
        if t.database:
            params["database"] = t.database
        params.setdefault("charset", DEFAULT_CHARSET)
        params["local_infile"] = False
        params["autocommit"] = False
        if plan.secret is not None:
            params["password"] = plan.secret.reveal()
        return params

    def default_connect(self, **params: Any) -> Any:
        try:
            pymysql = importlib.import_module("pymysql")
        except ImportError:
            raise ShapeError(
                "the mysql sink needs PyMySQL: pip install 'sqllocks-shape[mysql]'"
            ) from None
        return pymysql.connect(**params)

    def ddl_is_transactional(self) -> bool:
        return False

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
        columns = ", ".join(_sql.quote(n, self.dialect) for n in names)
        marks = ", ".join(["%s"] * len(names))
        # Identifiers only, each checked and quoted; every value is a bound parameter.
        statement = f"INSERT INTO {qualified} ({columns}) VALUES ({marks})"  # nosec B608
        converters = _sql.converters_for(first.schema, self.dialect)
        step = min(plan.batch_size, plan.commit_rows or plan.batch_size)

        def rows() -> Iterator[tuple[Any, ...]]:
            for batch in chain_batches(first, rest):
                _sql.check_batch(first.schema, batch, plan.table)
                yield from _sql.row_iterator(batch, self.dialect, converters)

        total = 0
        since_commit = 0
        cur = conn.cursor()
        try:
            for group in chunks(rows(), step):
                piece = list(group)
                cur.executemany(statement, piece)
                total += len(piece)
                since_commit += len(piece)
                if plan.commit_rows and since_commit >= plan.commit_rows:
                    conn.commit()
                    progress[0] = total
                    since_commit = 0
        finally:
            cur.close()
        if plan.commit_rows and since_commit:
            conn.commit()
            progress[0] = total
        return total
