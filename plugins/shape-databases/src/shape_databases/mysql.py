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

TLS. A host that is not loopback (``localhost``, ``127.0.0.0/8``, ``::1``) is reached over TLS
with certificate and host name verification: the system CA store, or ``ssl_ca`` when given.
``ssl=false`` or ``ssl-mode=DISABLED`` in the URI is the explicit opt-out; ``ssl-mode=VERIFY_CA``
skips the host name check. Giving ``ssl_verify_cert`` / ``ssl_verify_identity`` hands the driver
its own options unchanged. A TLS failure names the host and the opt-out.

``schema_name`` is the MySQL database to write into (default: the one in the URI). NaN and
infinity, which MySQL cannot store, are written as NULL, as the ``sql`` sink's script does.
Options are those of the PostgreSQL sink (see :mod:`shape_databases.postgres`).
"""

from __future__ import annotations

import importlib
import ssl
from collections.abc import Iterator
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError

from . import _sql
from ._base import (
    DatabaseSink,
    Plan,
    as_bool,
    as_int,
    as_text,
    chain_batches,
    chunks,
    is_loopback,
    one_of,
)

DEFAULT_CHARSET = "utf8mb4"
SSL_MODES = ("DISABLED", "VERIFY_CA", "VERIFY_IDENTITY")


def _ssl_mode(value: str) -> str:
    return one_of(*SSL_MODES)(value.upper())


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
        "ssl": as_bool,
        "ssl-mode": _ssl_mode,
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
        self._apply_tls(plan, params)
        params["local_infile"] = False
        params["autocommit"] = False
        if plan.secret is not None:
            params["password"] = plan.secret.reveal()
        return params

    def _tls_off(self, plan: Plan) -> bool:
        params = plan.target.params
        return params.get("ssl") is False or params.get("ssl-mode") == "DISABLED"

    def tls_opt_out(self, plan: Plan) -> str | None:
        t = plan.target
        # The URI chose the TLS settings itself (including the driver's own ssl_* options).
        if is_loopback(t.host) or self._tls_off(plan) or self._tls_chosen(plan):
            return None
        return "ssl=false (or ssl-mode=DISABLED)"

    @staticmethod
    def _tls_chosen(plan: Plan) -> bool:
        params = plan.target.params
        return "ssl_verify_cert" in params or "ssl_verify_identity" in params

    def _apply_tls(self, plan: Plan, params: dict[str, Any]) -> None:
        """Translate the URI's TLS keys; verified TLS is the default for a non-loopback host."""
        for key in ("ssl", "ssl-mode"):
            params.pop(key, None)
        if self._tls_off(plan):
            params["ssl_disabled"] = True
            return
        if is_loopback(plan.target.host) or self._tls_chosen(plan):
            return  # today's behaviour: the driver's options as the URI gave them
        ca = params.pop("ssl_ca", None)
        cert = params.pop("ssl_cert", None)
        key = params.pop("ssl_key", None)
        try:
            # check_hostname and CERT_REQUIRED are on in a default context; without ssl_ca the
            # system CA store is the trust store. (PyMySQL's own ``ssl`` dict would skip
            # verification when it is given no CA, so a context is passed instead.)
            ctx = ssl.create_default_context(cafile=ca)
            if plan.target.params.get("ssl-mode") == "VERIFY_CA":
                ctx.check_hostname = False
            if cert:
                ctx.load_cert_chain(cert, key)
        except (OSError, ssl.SSLError) as exc:
            raise ShapeError(f"cannot set up TLS for {plan.target.label}: {exc}") from None
        params["ssl"] = ctx

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
