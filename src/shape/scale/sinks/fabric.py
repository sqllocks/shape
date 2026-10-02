"""The Fabric sinks: Lakehouse, Warehouse, SQL Database and KQL (Eventhouse).

Each is a :class:`~shape.scale.sinks.writer.WriterSink` over the matching ``shape-fabric`` writer
(``shape_fabric.sinks``: the Lakehouse files writer, the Warehouse writer (``COPY INTO`` from a
staging path), the SQL Database writer and the Eventhouse writer). The plugin is imported when the
sink opens, so importing this module needs nothing installed and a missing plugin is a clear error
at ``open``, not at import. ``writer`` replaces the plugin's writer (a recording stand-in in the
contract tests); ``writer_options`` adds options for it (a ``credential``, or a ``connection``).

A database table is created with the key and column types of the generation schema. Sign-in is
the connection string's (or the ``credential`` option's): the ``--auth`` modes arrive with the
Fabric auth work package.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from shape.scale.sinks.writer import WriterSink

LAKEHOUSE_FORMATS = ("parquet", "csv", "jsonl")
WRITE_MODES = ("create", "append", "truncate", "replace")


def plugin_sink(name: str) -> Any:
    """The ``shape-fabric`` sink class ``name`` (``LakehouseSink`` ...), made."""
    try:
        sinks = importlib.import_module("shape_fabric.sinks")
    except ImportError as exc:
        raise ImportError(
            f"the {name} sink needs the shape-fabric plugin: pip install 'sqllocks-shape[fabric]'"
        ) from exc
    return getattr(sinks, name)()


def _check_mode(mode: str) -> str:
    if mode not in WRITE_MODES:
        raise ValueError(f"unknown write_mode {mode!r}; choose one of {', '.join(WRITE_MODES)}")
    return mode


class LakehouseSink(WriterSink):
    """Tables as files in a Lakehouse ``Files`` area, ``<base_path>/<table>/part-0001.<format>``.

    ``base_path`` is a local folder or an ``abfss://`` / ``onelake://`` path."""

    def __init__(
        self,
        base_path: str,
        format: str = "parquet",
        *,
        writer: Any = None,
        writer_options: Mapping[str, Any] | None = None,
    ) -> None:
        if format not in LAKEHOUSE_FORMATS:
            raise ValueError(
                f"unknown lakehouse format {format!r}; choose one of {LAKEHOUSE_FORMATS}"
            )
        if not base_path:
            raise ValueError("the lakehouse sink needs base_path")
        super().__init__(
            writer or (lambda: plugin_sink("LakehouseSink")),
            base_path,
            {"format": format, **dict(writer_options or {})},
            name="lakehouse",
        )
        self.format = format


class _DatabaseSink(WriterSink):
    """A SQL destination: tables get the primary key and column types of the schema."""

    def options_for(self, table: str) -> dict[str, Any]:
        options = super().options_for(table)
        if self._schema is not None and table in self._schema.tables:
            from shape.generation.output import sql_options

            options.update(sql_options(self._schema, table))
        return options


def _database_uri(connection_string: str, scheme: str) -> tuple[str, dict[str, Any]]:
    """A ``warehouse://host/db`` style URI is used as it is; anything else is an ODBC connection
    string, passed as the ``connection_string`` option (and kept out of the URI)."""
    if urlsplit(connection_string).scheme == scheme:
        return connection_string, {}
    return f"{scheme}://configured", {"connection_string": connection_string}


class WarehouseSink(_DatabaseSink):
    """Tables loaded into a Fabric Warehouse with ``COPY INTO`` from Parquet staged at
    ``staging_path`` (an ``abfss://`` or ``onelake://`` path)."""

    def __init__(
        self,
        connection_string: str,
        staging_path: str,
        schema_name: str = "dbo",
        write_mode: str = "create",
        chunk_size: int = 1_000_000,
        *,
        writer: Any = None,
        writer_options: Mapping[str, Any] | None = None,
    ) -> None:
        if not connection_string or not staging_path:
            raise ValueError("the warehouse sink needs connection_string and staging_path")
        uri, extra = _database_uri(connection_string, "warehouse")
        super().__init__(
            writer or (lambda: plugin_sink("WarehouseSink")),
            uri,
            {
                **extra,
                "staging_path": staging_path,
                "schema_name": schema_name,
                "write_mode": _check_mode(write_mode),
                "chunk_rows": int(chunk_size),
                **dict(writer_options or {}),
            },
            name="warehouse",
        )


class SqlDatabaseSink(_DatabaseSink):
    """Tables inserted into a Fabric SQL Database (or Azure SQL)."""

    def __init__(
        self,
        connection_string: str,
        schema_name: str = "dbo",
        write_mode: str = "create",
        batch_size: int = 5_000,
        *,
        writer: Any = None,
        writer_options: Mapping[str, Any] | None = None,
    ) -> None:
        if not connection_string:
            raise ValueError("the sql_database sink needs connection_string")
        uri, extra = _database_uri(connection_string, "sql-database")
        super().__init__(
            writer or (lambda: plugin_sink("SqlDatabaseSink")),
            uri,
            {
                **extra,
                "schema_name": schema_name,
                "write_mode": _check_mode(write_mode),
                "batch_size": int(batch_size),
                **dict(writer_options or {}),
            },
            name="sql_database",
        )


class KqlSink(WriterSink):
    """Tables ingested into a Fabric Eventhouse (KQL database), one KQL table per table, named
    ``<table_prefix><table>``."""

    def __init__(
        self,
        cluster_uri: str,
        database: str,
        table_prefix: str = "",
        write_mode: str = "create",
        *,
        writer: Any = None,
        writer_options: Mapping[str, Any] | None = None,
    ) -> None:
        if not cluster_uri or not database:
            raise ValueError("the kql sink needs cluster_uri and database")
        parts = urlsplit(cluster_uri if "://" in cluster_uri else f"//{cluster_uri}")
        if not parts.netloc:
            raise ValueError(f"cluster_uri is not a URI: {cluster_uri!r}")
        query = "?tls=false" if parts.scheme == "http" else ""
        self._prefix = table_prefix
        super().__init__(
            writer or (lambda: plugin_sink("EventhouseSink")),
            f"eventhouse://{parts.netloc}/{database}{query}",
            {"write_mode": _check_mode(write_mode), **dict(writer_options or {})},
            name="kql",
        )

    def options_for(self, table: str) -> dict[str, Any]:
        options = super().options_for(table)
        if self._prefix:
            options["kql_table"] = f"{self._prefix}{table}"
        return options
