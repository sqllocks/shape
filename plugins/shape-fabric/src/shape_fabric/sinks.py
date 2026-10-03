"""``Sink`` adapters over the writers, for code that works with the ``shape.sinks`` Protocol.

Each class has the Protocol's ``write(uri, table, batches, **options) -> int``: one table per call,
the writer built from the URI and the options and closed afterwards. Two are registered as
``shape.sinks`` entry points: ``sqlserver`` (:class:`SqlServerSink`, schemes ``mssql`` and
``sqlserver``) and ``warehouse`` (:class:`WarehouseSink`). The others are not; the scale router's
sinks (and ``shape generate``) choose the names under which they appear. Use the writers
directly (``LakehouseWriter``, ``SqlDatabaseWriter``, ``WarehouseWriter``, ``EventhouseWriter``,
``EventstreamWriter``) when you write several tables over one connection.

=================  =============================================  =============================
class              URI                                            options
=================  =============================================  =============================
LakehouseSink      folder: path, ``abfss://``, ``onelake://``     format, file_name, directory,
                                                                  schema, credential, filesystem
SqlServerSink      ``mssql://[user@]<host>[:port]/<database>``    the SQL database ones, + user,
                   (or ``sqlserver://``), ``?schema=&write_mode=  password, driver, encrypt,
                   &batch_size=&commit_rows=``                    trust_server_certificate,
                                                                  timeout, commit_rows
SqlDatabaseSink    ``sql-database://<host>/<database>``           connection_string, credential,
                   (or any URI + ``connection_string``)           connection, write_mode,
                                                                  batch_size, schema_name,
                                                                  columns, primary_key, schema
WarehouseSink      ``warehouse://<host>/<database>``              the above, + staging_path,
                                                                  chunk_rows (not batch_size)
EventhouseSink     ``eventhouse://<host>/<database>[/<table>]``   write_mode, kql_table, token,
                                                                  credential, max_request_bytes,
                                                                  schema
EventstreamSink    ``eventstream://<name>[/<entity>]``            connection_string, envelope,
                                                                  partition_key
=================  =============================================  =============================
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

import pyarrow as pa  # type: ignore[import-untyped]
from shape_sqlserver.sql import (
    build_connection_string,  # type: ignore[import-untyped,unused-ignore]
)

from shape.errors import ShapeError

from .eventhouse_writer import EventhouseWriter
from .eventstream_writer import EventstreamWriter
from .lakehouse import LakehouseWriter
from .sqldb import SqlDatabaseWriter
from .warehouse import WarehouseWriter

log = logging.getLogger(__name__)


def _take(options: dict[str, Any], names: Iterable[str]) -> dict[str, Any]:
    return {k: options.pop(k) for k in tuple(names) if k in options}


def _unknown(sink: str, options: dict[str, Any]) -> None:
    if options:
        raise ShapeError(f"unknown {sink} sink options: {sorted(options)}")


def connection_string_for(uri: str, options: dict[str, Any], scheme: str) -> str | None:
    """The ``connection_string`` option, else a server and database taken from ``uri``."""
    given = options.pop("connection_string", None)
    if given:
        return str(given)
    if options.get("connection") is not None:
        return None
    parts = urlsplit(uri)
    if "@" in parts.netloc:
        # Never echo the URI here: its user info may hold a password.
        raise ShapeError(
            f"a {scheme}:// URI holds no user or password: sign in with credential= (Microsoft "
            "Entra), or give connection_string"
        )
    try:
        port = parts.port
    except ValueError:
        raise ShapeError(f"the {scheme}:// URI has no usable port") from None
    database = unquote(parts.path.lstrip("/")).split("/")[0]
    if parts.scheme != scheme or not parts.hostname or not database:
        raise ShapeError(
            f"give connection_string, or a URI of the form {scheme}://<host>[:port]/<database>: "
            f"{uri!r}"
        )
    server = f"{parts.hostname},{port}" if port else parts.hostname
    return str(build_connection_string(server, database))


class LakehouseSink:
    name = "lakehouse"
    schemes = ("abfss", "onelake", "file")

    def write(self, uri: str, table: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        opts = dict(options)
        writer = LakehouseWriter(
            uri,
            format=opts.pop("format", "parquet"),
            credential=opts.pop("credential", None),
            filesystem=opts.pop("filesystem", None),
        )
        keys = _take(opts, ("file_name", "directory", "schema"))
        _unknown(self.name, opts)
        return writer.write_table(table, batches, **keys)


class SqlDatabaseSink:
    name = "sql-database"
    schemes = ("sql-database",)

    def write(self, uri: str, table: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        opts = dict(options)
        conn = connection_string_for(uri, opts, "sql-database")
        with SqlDatabaseWriter(
            conn,
            credential=opts.pop("credential", None),
            connection=opts.pop("connection", None),
            schema_name=opts.pop("schema_name", "dbo"),
        ) as writer:
            keys = _take(opts, ("write_mode", "batch_size", "columns", "primary_key", "schema"))
            _unknown(self.name, opts)
            return writer.write_table(table, batches, **keys)


class WarehouseSink:
    name = "warehouse"
    schemes = ("warehouse",)

    def write(self, uri: str, table: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        opts = dict(options)
        conn = connection_string_for(uri, opts, "warehouse")
        with WarehouseWriter(
            conn,
            opts.pop("staging_path", None),
            credential=opts.pop("credential", None),
            connection=opts.pop("connection", None),
            filesystem=opts.pop("filesystem", None),
            schema_name=opts.pop("schema_name", "dbo"),
        ) as writer:
            keys = _take(opts, ("write_mode", "chunk_rows", "columns", "primary_key", "schema"))
            _unknown(self.name, opts)
            return writer.write_table(table, batches, **keys)


_CONN_KEYS = (
    "user",
    "password",
    "auth",
    "driver",
    "encrypt",
    "trust_server_certificate",
    "timeout",
)
_URI_KEYS = frozenset(
    {*_CONN_KEYS, "schema", "table", "write_mode", "batch_size", "commit_rows"}
) - {"password"}
_WRITE_KEYS = ("write_mode", "batch_size", "commit_rows", "columns", "primary_key", "schema")


def _flag(name: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    raise ShapeError(f"{name} must be true or false")


def _whole(name: str, value: Any) -> int:
    try:
        number = int(str(value))
    except ValueError:
        raise ShapeError(f"{name} must be a positive integer") from None
    if isinstance(value, bool) or number < 1:
        raise ShapeError(f"{name} must be a positive integer")
    return number


def _uri_password(uri: str) -> str | None:
    try:
        password = urlsplit(uri).password
    except ValueError:
        return None
    return unquote(password) if password else None


_PWD = re.compile(r"(?i)\b(?:pwd|password|access ?token)\s*=\s*(\{(?:[^}]|\}\})*\}|[^;]*)")


def _pwd_values(connection_string: str) -> list[str]:
    """The password and token values inside a connection string (so they can be masked)."""
    found = []
    for match in _PWD.finditer(connection_string):
        value = match.group(1).strip()
        if value.startswith("{") and value.endswith("}"):
            value = value[1:-1].replace("}}", "}")
        if value:
            found.append(value)
    return found


class SqlServerSink:
    """Rows into a live SQL Server, Azure SQL database, Fabric SQL database or Warehouse.

    ``mssql://[user[:password]@]host[:port]/database?schema=dbo&write_mode=append`` (or
    ``sqlserver://``) plus the options below; an option beats the same name in the URI. The rows
    go through :class:`~shape_fabric.sqldb.SqlDatabaseWriter` (parameterised bulk ``INSERT``;
    ``write_mode`` is ``create`` (the default), ``append``, ``truncate`` or ``replace``).

    * ``credential``: signs in with Microsoft Entra (``get_token(scope)`` or a function
      ``scope -> token``); without it the login is ``user`` and ``password`` (a SQL login).
    * ``connection_string`` (ODBC or ADO.NET form) or an open ``connection`` replace the URI's
      host and database.
    * ``commit_rows``: commit every N rows while the batches are consumed, so a reader sees them
      as they arrive (streaming); the default is one transaction for the whole call.
    * ``batch_size`` (rows per round trip), ``schema_name`` (default ``dbo``), ``columns``,
      ``primary_key`` and ``schema`` (an Arrow schema, to create an empty table) as the writer.

    A secret (``password``, ``connection_string``, a password in the URI) is never part of an
    exception message or a log record; give them as options or in the environment of the caller,
    never on a command line. ``connect`` is a test seam, with the signature of
    ``shape_fabric._tsql.connect``.
    """

    name = "sqlserver"
    schemes = ("mssql", "sqlserver")

    def __init__(self, *, connect: Callable[..., Any] | None = None) -> None:
        self._connect = connect

    def write(self, uri: str, table: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        opts = dict(options)
        secrets = [str(v) for k in ("password", "connection_string") if (v := opts.get(k))]
        secrets += _pwd_values(str(opts.get("connection_string") or ""))
        if (from_uri := _uri_password(uri)) is not None:
            secrets.append(from_uri)
        masked: BaseException | None = None
        try:
            return self._write(uri, table, batches, opts)
        except Exception as exc:
            chain: list[BaseException] = []
            seen: BaseException | None = exc
            while seen is not None and len(chain) < 8:
                chain.append(seen)
                seen = seen.__cause__ or seen.__context__
            if not any(sec in str(e) for e in chain for sec in secrets):
                raise
            # a driver or a caller put a secret into a message: mask it and drop the chain
            text = str(exc)
            for secret in secrets:
                text = text.replace(secret, "***")
            masked = type(exc)(text)
        raise masked from None  # outside the handler, so no __context__ keeps the original

    def _write(
        self, uri: str, table: str, batches: Iterable[pa.RecordBatch], opts: dict[str, Any]
    ) -> int:
        credential = opts.pop("credential", None)
        connection = opts.pop("connection", None)
        schema_name = opts.pop("schema_name", None)
        given = opts.pop("connection_string", None)
        conn_opts = {k: opts.pop(k) for k in _CONN_KEYS if k in opts}
        write_opts = {k: opts.pop(k) for k in _WRITE_KEYS if k in opts}
        _unknown(self.name, opts)
        conn = str(given) if given else None
        # The URI's write options and schema apply either way; a connection string (or an open
        # connection) replaces only how the sink connects.
        query, parts = self._parse(uri)
        if "schema" in query and schema_name is None:
            schema_name = query.pop("schema")
        for key in ("write_mode", "batch_size", "commit_rows"):
            if key in query:
                write_opts.setdefault(key, query[key])
        if not given:
            for key in _CONN_KEYS:
                if key in query:
                    conn_opts.setdefault(key, query[key])
            if connection is None:
                conn = self._connection_string(parts, conn_opts, credential)
        for key in ("batch_size", "commit_rows"):
            if key in write_opts:
                write_opts[key] = _whole(key, write_opts[key])
        with SqlDatabaseWriter(
            conn,
            credential=credential,
            connection=connection,
            connect=self._connect,
            schema_name=str(schema_name or "dbo"),
        ) as writer:
            log.debug("writing table %r to %s", table, writer.destination)
            rows = writer.write_table(table, batches, **write_opts)
            log.debug("wrote %d rows of table %r to %s", rows, table, writer.destination)
            return rows

    def _parse(self, uri: str) -> tuple[dict[str, str], Any]:
        parts = urlsplit(uri)
        query: dict[str, str] = {}
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if key not in _URI_KEYS:
                secret = any(w in key.lower() for w in ("pw", "pass", "secret", "token"))
                hint = "; a password is never accepted in the query" if secret else ""
                raise ShapeError(f"unknown query parameter {key!r} in the sink URI{hint}")
            query[key] = value
        query.pop("table", None)
        return query, parts

    def _connection_string(self, parts: Any, conn_opts: dict[str, Any], credential: Any) -> str:
        try:
            port = parts.port
        except ValueError:
            raise ShapeError("the sink URI has no usable port") from None
        database = unquote(parts.path.lstrip("/")).split("/")[0]
        if parts.scheme not in self.schemes or not parts.hostname or not database:
            raise ShapeError(
                "give connection_string or connection, or a URI of the form "
                "mssql://<host>[:port]/<database>"
            )
        if str(conn_opts.pop("auth", "sql")).lower() != "sql":
            raise ShapeError(
                "the sink signs in with a SQL login (user and password) or with credential=; "
                "pass credential= for Microsoft Entra"
            )
        user = conn_opts.pop("user", None) or (unquote(parts.username) if parts.username else None)
        password = conn_opts.pop("password", None) or _uri_password(parts.geturl())
        if credential is not None and (user or password):
            raise ShapeError("use either credential= or a user and password, not both")
        extra: dict[str, Any] = {}
        if "driver" in conn_opts:
            extra["driver"] = str(conn_opts["driver"])
        for key in ("encrypt", "trust_server_certificate"):
            if key in conn_opts:
                extra[key] = _flag(key, conn_opts[key])
        if "timeout" in conn_opts:
            extra["timeout"] = _whole("timeout", conn_opts["timeout"])
        server = f"{parts.hostname},{port}" if port else str(parts.hostname)
        return str(build_connection_string(server, database, user=user, password=password, **extra))


class EventhouseSink:
    name = "eventhouse"
    schemes = ("eventhouse",)

    def write(self, uri: str, table: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        opts = dict(options)
        writer = EventhouseWriter(
            uri, token=opts.pop("token", None), credential=opts.pop("credential", None)
        )
        keys = _take(opts, ("write_mode", "kql_table", "max_request_bytes", "schema"))
        _unknown(self.name, opts)
        return writer.write_table(table, batches, **keys)


class EventstreamSink:
    name = "eventstream"
    schemes = ("eventstream",)

    def write(self, uri: str, table: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        opts = dict(options)
        writer = EventstreamWriter(
            uri,
            connection_string=opts.pop("connection_string", None),
            envelope=opts.pop("envelope", "flat"),
            partition_key=opts.pop("partition_key", "table"),
        )
        _unknown(self.name, opts)
        try:
            return writer.write_table(table, batches)
        finally:
            writer.close()
