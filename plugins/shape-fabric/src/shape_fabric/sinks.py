"""``Sink`` adapters over the writers, for code that works with the ``shape.sinks`` Protocol.

Each class has the Protocol's ``write(uri, table, batches, **options) -> int``: one table per call,
the writer built from the URI and the options and closed afterwards. They are **not** registered as
entry points here; the scale router's sinks (and ``shape generate``) choose the names under which
they appear. Use the writers directly (``LakehouseWriter``, ``SqlDatabaseWriter``,
``WarehouseWriter``, ``EventhouseWriter``, ``EventstreamWriter``) when you write several tables
over one connection.

=================  =============================================  =============================
class              URI                                            options
=================  =============================================  =============================
LakehouseSink      folder: path, ``abfss://``, ``onelake://``     format, file_name, directory,
                                                                  schema, credential, filesystem
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

from collections.abc import Iterable
from typing import Any
from urllib.parse import unquote, urlsplit

import pyarrow as pa  # type: ignore[import-untyped]
from shape_sqlserver.sql import build_connection_string

from shape.errors import ShapeError

from .eventhouse_writer import EventhouseWriter
from .eventstream_writer import EventstreamWriter
from .lakehouse import LakehouseWriter
from .sqldb import SqlDatabaseWriter
from .warehouse import WarehouseWriter


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
    database = unquote(parts.path.lstrip("/")).split("/")[0]
    if parts.scheme != scheme or not parts.netloc or not database:
        raise ShapeError(
            f"give connection_string, or a URI of the form {scheme}://<host>/<database>: {uri!r}"
        )
    return build_connection_string(parts.netloc, database)


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
