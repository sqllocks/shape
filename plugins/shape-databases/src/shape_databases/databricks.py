"""Databricks sink: ``databricks://<host>/<http_path>?catalog=CAT&schema=SCH``.

Writes Delta tables in Unity Catalog through a SQL warehouse, with ``databricks-sql-connector``
(``pip install 'sqllocks-shape-databases[databricks]'``), loaded only when a connection opens.
``<http_path>`` is the warehouse's HTTP path without its leading slash, for example
``databricks://adb-123.4.azuredatabricks.net/sql/1.0/warehouses/abc123?catalog=main&schema=demo``.

Rows go in as multi-row ``INSERT`` statements of ``batch_size`` rows (default 1,000), every value
a bound parameter (``?`` markers; the connector's native parameters). Dates, timestamps and
binary values are bound as text and converted in the statement (``CAST(? AS DATE)``,
``CAST(? AS TIMESTAMP_NTZ)``, ``CAST(? AS TIMESTAMP)`` from UTC, ``UNHEX(?)``), so a value
reaches the table exactly, whatever the session time zone.

Transactions. A SQL warehouse commits every statement; there is no rollback. ``commit_rows`` is
accepted and is the batch boundary: every statement is already a commit, so ``commit_rows`` does
not shrink statements (``batch_size`` does); it makes a statement carry no rows from the next
input batch (rows become visible batch by batch while a stream runs), and a failure then keeps
the table and the rows already inserted, with :class:`~shape_databases.WriteError` saying how
many (``rows_committed``).
Without it, a failure drops a table this call created, and leaves in a table that already
existed (``append``) the rows of the statements that had run, which the error also counts.
``replace`` has already dropped the old table, and ``truncate`` has already emptied it.

Names. Unity Catalog stores table and schema names in lower case, so a name with capitals, a
space, ``.``, ``/`` or a backtick is refused before any connection, as is a column name with
a space or one of ``,;{}()=``, a tab or a newline (which Delta cannot hold), and any name over 255
characters. Column names are case-insensitive: two that differ only in case are refused.

Sign-in (a token is never accepted in the URI): a personal access token, the ``token`` option (a
reference or the value) or ``DATABRICKS_TOKEN``; or OAuth machine-to-machine, ``client_id`` (an
option, or in the URI) with ``client_secret`` (a reference or the value), exchanged at the
workspace's ``/oidc/v1/token`` endpoint for a token that lasts about an hour.

Options: ``write_mode``, ``schema_name``, ``table_prefix``, ``commit_rows``, ``columns``,
``primary_key``, ``schema`` (see :mod:`shape_databases.postgres`) and ``batch_size``.
"""

from __future__ import annotations

import base64
import importlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.types as pat  # type: ignore[import-untyped]

from shape.errors import ShapeError

from . import _cloud, _sql
from ._auth import Secret, _resolve_reference, resolve_password
from ._base import DatabaseSink, Plan, Target, as_text, chain_batches, chunks, parse_uri
from .errors import CredentialError

INSTALL = "pip install 'sqllocks-shape-databases[databricks]'"
_HOST = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$")
DEFAULT_BATCH_SIZE = 1000
Binding = tuple[str, Callable[[Any], Any] | None]


def binding(field: pa.Field) -> Binding:
    """The placeholder a column's value goes into and the conversion it needs first."""
    t = field.type
    if pat.is_timestamp(t):
        if t.tz is None:
            return "CAST(? AS TIMESTAMP_NTZ)", lambda v: None if v is None else v.isoformat(" ")
        return "CAST(? AS TIMESTAMP)", lambda v: None if v is None else v.isoformat()
    if pat.is_date(t):
        return "CAST(? AS DATE)", lambda v: None if v is None else v.isoformat()
    if pat.is_binary(t) or pat.is_large_binary(t) or pat.is_fixed_size_binary(t):
        return "UNHEX(?)", lambda v: None if v is None else bytes(v).hex()
    if pat.is_uint64(t):
        return "CAST(? AS DECIMAL(20,0))", lambda v: None if v is None else str(v)
    if _cloud.is_nested(t):
        return "?", lambda v: None if v is None else _sql._json(v)  # noqa: SLF001
    return "?", None


def oauth_token(host: str, client_id: str, client_secret: str, *, opener: Any = None) -> str:
    """A workspace access token for a service principal (OAuth machine-to-machine)."""
    if not _HOST.match(host):
        raise ShapeError(f"not a usable Databricks host name: {host!r}")
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    request = urllib.request.Request(  # noqa: S310 - https only, host checked above
        f"https://{host}/oidc/v1/token",
        data=urllib.parse.urlencode(
            {"grant_type": "client_credentials", "scope": "all-apis"}
        ).encode(),
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    failure: CredentialError | None = None
    try:
        open_url = opener or urllib.request.urlopen  # noqa: S310
        with open_url(request, timeout=30) as response:  # nosec B310
            token = json.loads(response.read().decode("utf-8")).get("access_token")
        if isinstance(token, str) and token:
            return token
        failure = CredentialError(f"the OAuth endpoint of {host} gave no access token")
    except urllib.error.HTTPError as exc:
        failure = CredentialError(f"OAuth sign-in to {host} was refused (HTTP {exc.code})")
    except Exception as exc:  # noqa: BLE001 - the text could echo what was sent
        failure = CredentialError(f"OAuth sign-in to {host} failed ({type(exc).__name__})")
    raise failure


class _Session:
    """The connector's connection with the ``commit`` and ``rollback`` of DB-API: a warehouse
    commits each statement, so both do nothing."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def cursor(self) -> Any:
        return self._conn.cursor()

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        self._conn.close()


class DatabricksSink(DatabaseSink):
    """``RecordBatch``es for one table to a Delta table with batched, bound ``INSERT``."""

    name = "databricks"
    schemes = ("databricks",)
    dialect = "databricks"
    password_env = ("DATABRICKS_TOKEN",)
    uri_params = {"catalog": as_text, "schema": as_text, "client_id": as_text}
    exists_sql = (
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = COALESCE(?, current_schema()) AND table_name = ?"
    )

    # -- plan --------------------------------------------------------------------------------
    def parse_target(self, uri: str) -> Target:
        target = parse_uri(uri, self.schemes, self.uri_params)
        if not target.host or not _HOST.match(target.host):
            raise ShapeError(
                "the destination needs a workspace host: databricks://<host>/<http_path>"
            )
        if target.user or target.port:
            raise ShapeError(
                "the destination must be databricks://<host>/<http_path>: no user and no port "
                "(sign in with token or client_id and client_secret)"
            )
        path = (target.database or "").strip("/")
        if not path:
            raise ShapeError("the destination needs the warehouse's HTTP path after the host")
        params = dict(target.params)
        params["http_path"] = "/" + path
        for key, kind in (("catalog", "catalog"), ("schema", "schema")):
            if key in params:
                _sql.check_identifier(params[key], kind, self.dialect)
        return Target(target.host, None, None, path, params)

    def resolve_auth(self, options: Mapping[str, Any]) -> tuple[Secret | None, dict[str, Any]]:
        token = options.get("token")
        client_secret = options.get("client_secret")
        if token is not None and client_secret is not None:
            raise CredentialError("give a token or client_id and client_secret, not both")
        if client_secret is not None:
            if not isinstance(client_secret, str) or not client_secret:
                raise CredentialError("client_secret must be a non-empty string or a reference")
            return None, {
                "client_secret": Secret(_resolve_reference(client_secret) or client_secret)
            }
        given: dict[str, Any] = {"password": token} if token is not None else {}
        return resolve_password(given, self.password_env), {}

    def plan(self, uri: str, table: str, options: Mapping[str, Any]) -> Plan:
        plan = super().plan(uri, table, options)
        client_id = plan.options.get("client_id") or plan.target.params.get("client_id")
        if "client_secret" in plan.auth and not client_id:
            raise CredentialError("client_secret needs a client_id")
        if client_id and "client_secret" not in plan.auth:
            raise CredentialError("client_id needs a client_secret (a reference or the value)")
        if client_id:
            plan.target.params["client_id"] = str(client_id)
        # the default batch size of this sink, not the MySQL one
        plan.batch_size = int(
            _sql.positive_int(options.get("batch_size", DEFAULT_BATCH_SIZE), "batch_size") or 0
        )
        return plan

    def check_schema(self, schema: pa.Schema, plan: Plan) -> None:
        _cloud.check_types(schema, self.dialect, plan.columns, plan.primary_key)

    def create_table_sql(self, plan: Plan, schema: pa.Schema, first: pa.RecordBatch | None) -> str:
        qualified = _sql.qualified(plan.schema_name, plan.table, self.dialect)
        return _cloud.create_table_sql(
            qualified, schema, self.dialect, plan.columns, plan.primary_key
        )

    # -- connection --------------------------------------------------------------------------
    def connect_params(self, plan: Plan) -> dict[str, Any]:
        t = plan.target
        params: dict[str, Any] = {"server_hostname": t.host, "http_path": t.params["http_path"]}
        for key in ("catalog", "schema"):
            if key in t.params:
                params[key] = t.params[key]
        if plan.secret is not None:
            params["access_token"] = plan.secret.reveal()
        if "client_secret" in plan.auth:
            params["client_id"] = t.params["client_id"]
            params["client_secret"] = plan.auth["client_secret"].reveal()
        return params

    def default_connect(self, **params: Any) -> Any:
        try:
            dbsql = importlib.import_module("databricks.sql")
        except ImportError:
            raise ShapeError(
                f"the databricks sink needs databricks-sql-connector: {INSTALL}"
            ) from None
        client_secret = params.pop("client_secret", None)
        client_id = params.pop("client_id", None)
        if client_secret is not None:
            params["access_token"] = oauth_token(
                params["server_hostname"], client_id, client_secret
            )
        elif not params.get("access_token"):
            raise CredentialError(
                "no Databricks credentials: give token, or client_id and client_secret, "
                "or set DATABRICKS_TOKEN"
            )
        return _Session(dbsql.connect(**params))

    def ddl_is_transactional(self) -> bool:
        return False

    def _exists(self, conn: Any, plan: Plan) -> bool:
        cur = conn.cursor()
        try:
            cur.execute(self.exists_sql, [plan.schema_name, plan.table])
            return bool(cur.fetchone() is not None)
        finally:
            cur.close()

    def _undo(self, conn: Any, plan: Plan, created: bool, committed: int) -> int:
        """There is nothing to roll back: every statement already committed. A table this call
        created is dropped unless ``commit_rows`` asked for rows to stay visible."""
        if conn is None or not created or plan.commit_rows:
            return committed
        try:
            cur = conn.cursor()
            cur.execute(_sql.drop_table_sql(plan.schema_name, plan.table, self.dialect))
        except Exception:  # noqa: BLE001, S110  # nosec B110
            return committed  # the original error is the one to report
        return 0

    # -- load --------------------------------------------------------------------------------
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
        bindings = [binding(f) for f in first.schema]
        row_marks = "(" + ", ".join(b[0] for b in bindings) + ")"
        converters = [b[1] for b in bindings]

        def converted(batch: pa.RecordBatch) -> Iterator[tuple[Any, ...]]:
            _sql.check_batch(first.schema, batch, plan.table)
            return _sql.row_iterator(_cloud.parquet_batch(batch), self.dialect, converters)

        def groups() -> Iterator[Iterator[tuple[Any, ...]]]:
            if not plan.commit_rows:
                yield from chunks(
                    (row for b in chain_batches(first, rest) for row in converted(b)),
                    plan.batch_size,
                )
                return
            # a statement never waits for the next input batch: every statement commits, so
            # rows become visible batch by batch while a stream runs
            for batch in chain_batches(first, rest):
                yield from chunks(converted(batch), plan.batch_size)

        total = 0
        cur = conn.cursor()
        try:
            for group in groups():
                piece = list(group)
                # identifiers only, each checked and quoted; every value is a bound parameter
                statement = (
                    f"INSERT INTO {qualified} ({columns}) VALUES "  # nosec B608
                    + ", ".join([row_marks] * len(piece))
                )
                cur.execute(statement, [v for row in piece for v in row])
                total += len(piece)
                progress[0] = total
        finally:
            cur.close()
        return total
