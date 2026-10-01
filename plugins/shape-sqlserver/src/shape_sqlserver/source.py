"""``mssql://`` source: one SQL Server table as a stream of Arrow record batches.

URI: ``mssql://SERVER[:PORT]/DATABASE?schema=dbo&table=orders``. The URI never carries a
secret. Options (all optional):

``connection``
    an open DB-API connection; reused and left open. With it the URI may be just
    ``mssql:///?table=orders``.
``connection_string``
    a full ODBC connection string, instead of building one from the URI.
``credentials`` / ``auth``
    a :class:`~shape_sqlserver.auth.Credentials`, or one of its method names (default ``cli``).
``user`` and ``password``
    a SQL login, used with ``auth="sql"`` (or when given, by default).
``batch_size``
    rows per record batch (default 65536).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import pyarrow as pa  # type: ignore[import-untyped]

from .auth import Credentials, connect
from .catalog import read_catalog
from .sql import (
    DEFAULT_SCHEMA,
    PRIMARY_KEY_QUERY,
    SqlServerError,
    build_connection_string,
    datetimeoffset_from_bytes,
    fetch_dicts,
    qualified_name,
    quote_ident,
    register_converters,
    sql_type_to_arrow,
)

SCHEME = "mssql"
DEFAULT_BATCH_SIZE = 65536


def parse_uri(uri: str) -> tuple[str, int | None, str | None, str, str]:
    """``(server, port, database, schema, table)`` of an ``mssql://`` URI."""
    parsed = urlparse(uri)
    if parsed.scheme != SCHEME:
        raise SqlServerError(f"{uri!r} is not an {SCHEME}:// URI")
    query = parse_qs(parsed.query)
    tables = query.get("table")
    if not tables or not tables[0]:
        raise SqlServerError(f"{uri!r} names no table; add ?table=NAME (and schema=NAME)")
    schema = query.get("schema", [DEFAULT_SCHEMA])[0] or DEFAULT_SCHEMA
    database = unquote(parsed.path.lstrip("/")) or None
    return parsed.hostname or "", parsed.port, database, schema, tables[0]


def normalize_value(value: Any, type_name: str) -> Any:
    """A driver value as Arrow expects it (``datetimeoffset`` arrives as raw bytes)."""
    if value is None:
        return None
    if type_name == "datetimeoffset" and isinstance(value, bytes | bytearray):
        return datetimeoffset_from_bytes(value)
    if isinstance(value, dt.datetime) and value.tzinfo is not None:
        return value.astimezone(dt.UTC)
    if type_name == "uniqueidentifier":
        return str(value)
    if type_name in ("binary", "varbinary", "image", "rowversion", "timestamp"):
        return bytes(value)
    return value


class SqlServerSource:
    """Reads a SQL Server table (``mssql://`` URI) as record batches."""

    name = "mssql"
    schemes = (SCHEME,)

    def __init__(self, connection_factory: Callable[[], Any] | None = None) -> None:
        # Opens the connection when neither ``connection`` nor ``connection_string`` is given
        # (tests, and hosts that manage their own connections).
        self._factory = connection_factory

    def can_open(self, uri: str) -> bool:
        parsed = urlparse(uri)
        return parsed.scheme == SCHEME and bool(parse_qs(parsed.query).get("table"))

    def _credentials(self, options: dict[str, Any]) -> Credentials:
        given = options.get("credentials")
        if isinstance(given, Credentials):
            return given
        auth = options.get("auth") or ("sql" if options.get("user") else "cli")
        return Credentials(
            method=str(auth),
            tenant_id=options.get("tenant_id"),
            client_id=options.get("client_id"),
            client_secret=options.get("client_secret"),
        )

    def _open(self, uri: str, options: dict[str, Any]) -> tuple[Any, bool]:
        """``(connection, owned)``."""
        if options.get("connection") is not None:
            register_converters(options["connection"])
            return options["connection"], False
        text = options.get("connection_string")
        if not text and self._factory is not None:
            return self._factory(), True
        if not text:
            server, port, database, _, _ = parse_uri(uri)
            if not server:
                raise SqlServerError(
                    "the URI names no server; use mssql://SERVER/DATABASE?table=NAME or pass "
                    "connection_string= or connection="
                )
            text = build_connection_string(
                f"{server},{port}" if port else server,
                database,
                user=options.get("user"),
                password=options.get("password"),
                trust_server_certificate=bool(options.get("trust_server_certificate", False)),
            )
        return connect(text, self._credentials(options)), True

    def schema(self, uri: str, **options: Any) -> pa.Schema:
        _, _, _, schema_name, table = parse_uri(uri)
        conn, owned = self._open(uri, options)
        try:
            cursor = conn.cursor()
            try:
                return self._schema(cursor, schema_name, table)
            finally:
                cursor.close()
        finally:
            if owned:
                conn.close()

    def _schema(self, cursor: Any, schema_name: str, table: str) -> pa.Schema:
        found = read_catalog(cursor, schema_name, [table]).tables
        if not found:
            raise SqlServerError(f"table {schema_name}.{table} not found")
        return pa.schema(
            [
                pa.field(
                    c.name,
                    sql_type_to_arrow(c.type_name, c.precision, c.scale),
                    nullable=c.is_nullable,
                )
                for c in found[0].columns
            ]
        )

    def read(self, uri: str, **options: Any) -> Iterator[pa.RecordBatch]:
        _, _, _, schema_name, table = parse_uri(uri)
        batch_size = int(options.get("batch_size", DEFAULT_BATCH_SIZE))
        if batch_size < 1:
            raise SqlServerError("batch_size must be a positive integer")
        conn, owned = self._open(uri, options)
        try:
            cursor = conn.cursor()
            try:
                found = read_catalog(cursor, schema_name, [table]).tables
                if not found:
                    raise SqlServerError(f"table {schema_name}.{table} not found")
                info = found[0]
                schema = self._schema(cursor, schema_name, table)
                cursor.execute(PRIMARY_KEY_QUERY, (info.object_id,))
                declared = [r["column_name"] for r in fetch_dicts(cursor)]
                sql = "SELECT " + ", ".join(quote_ident(c.name) for c in info.columns)
                sql += " FROM " + qualified_name(schema_name, table)
                if declared:
                    sql += " ORDER BY " + ", ".join(quote_ident(c) for c in declared)
                cursor.execute(sql)
                types = [c.type_name for c in info.columns]
                while True:
                    rows = cursor.fetchmany(batch_size)
                    if not rows:
                        break
                    columns = [
                        pa.array(
                            [normalize_value(row[i], types[i]) for row in rows],
                            type=schema.field(i).type,
                        )
                        for i in range(len(types))
                    ]
                    yield pa.RecordBatch.from_arrays(columns, schema=schema)
            finally:
                cursor.close()
        finally:
            if owned:
                conn.close()
