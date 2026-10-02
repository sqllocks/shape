"""The Fabric sinks: Lakehouse, Warehouse, SQL Database and KQL (Eventhouse).

Each is a :class:`~shape.scale.sinks.writer.WriterSink` over the matching ``shape.sinks`` writer:
the Lakehouse files writer, the Warehouse bulk writer (``COPY INTO`` from a staging path), the SQL
Database writer and the Eventhouse writer. The writers come from the ``shape-fabric`` plugin
(P6-07a); a sink looks its writer up when it opens, so importing this module needs nothing
installed and a missing plugin is a clear error at ``open``, not at import.

A Lakehouse path that is local, or a file format Shape writes itself, needs no plugin.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from shape.scale.sinks.writer import WriterSink

LAKEHOUSE_FORMATS = ("parquet", "csv", "tsv", "jsonl")


def _host_writer(name: str, what: str) -> Any:
    from shape.plugins.host import default_host

    host = default_host()
    writer = host.try_get("shape.sinks", name)
    if writer is None:
        raise ImportError(
            f"the {what} sink needs the {name!r} writer, which the shape-fabric plugin provides: "
            "pip install 'sqllocks-shape[fabric]'"
        )
    return writer


def _is_remote(path: str) -> bool:
    return "://" in path and not path.startswith("file://")


class LakehouseSink(WriterSink):
    """Tables as files in a Lakehouse ``Files`` area: ``<base_path>/<table>.<format>``.

    ``base_path`` is a local folder or an ``abfss://`` OneLake path (the latter needs the
    ``shape-fabric`` writer)."""

    def __init__(self, base_path: str, format: str = "parquet") -> None:
        if format not in LAKEHOUSE_FORMATS:
            raise ValueError(
                f"unknown lakehouse format {format!r}; choose one of {LAKEHOUSE_FORMATS}"
            )
        if not base_path:
            raise ValueError("the lakehouse sink needs base_path")
        uri = base_path if base_path.endswith(("/", "\\")) else base_path + "/"
        if _is_remote(base_path):
            super().__init__(
                lambda: _host_writer("lakehouse", "lakehouse"),
                uri,
                {"format": format},
                name="lakehouse",
            )
        else:
            from pathlib import Path

            Path(base_path).mkdir(parents=True, exist_ok=True)
            super().__init__(lambda: _host_writer(format, "lakehouse"), uri, name="lakehouse")
        self.format = format


class WarehouseSink(WriterSink):
    """Tables loaded into a Fabric Warehouse with ``COPY INTO`` from Parquet staged at
    ``staging_path`` (an ``abfss://`` OneLake path)."""

    def __init__(
        self,
        connection_string: str,
        staging_path: str,
        schema_name: str = "dbo",
        auth: str = "cli",
        chunk_size: int = 1_000_000,
        credentials: Mapping[str, str] | None = None,
    ) -> None:
        if not connection_string or not staging_path:
            raise ValueError("the warehouse sink needs connection_string and staging_path")
        super().__init__(
            lambda: _host_writer("warehouse", "warehouse"),
            connection_string,
            {
                "staging_path": staging_path,
                "schema_name": schema_name,
                "auth": auth,
                "chunk_rows": chunk_size,
                **dict(credentials or {}),
            },
            name="warehouse",
        )


class SqlDatabaseSink(WriterSink):
    """Tables inserted into a Fabric SQL Database (or Azure SQL)."""

    def __init__(
        self,
        connection_string: str,
        schema_name: str = "dbo",
        write_mode: str = "create_insert",
        batch_size: int = 5_000,
        auth: str = "cli",
        staging_path: str | None = None,
        credentials: Mapping[str, str] | None = None,
    ) -> None:
        if not connection_string:
            raise ValueError("the sql_database sink needs connection_string")
        options: dict[str, Any] = {
            "schema_name": schema_name,
            "write_mode": write_mode,
            "batch_size": batch_size,
            "auth": auth,
            **dict(credentials or {}),
        }
        if staging_path:
            options["staging_path"] = staging_path
        super().__init__(
            lambda: _host_writer("sql_database", "sql_database"),
            connection_string,
            options,
            name="sql_database",
        )


class KqlSink(WriterSink):
    """Tables ingested into a Fabric Eventhouse (KQL database)."""

    def __init__(
        self,
        cluster_uri: str,
        database: str,
        table_prefix: str = "",
        batch_size: int = 10_000,
        auth: str = "cli",
        credentials: Mapping[str, str] | None = None,
    ) -> None:
        if not cluster_uri or not database:
            raise ValueError("the kql sink needs cluster_uri and database")
        super().__init__(
            lambda: _host_writer("eventhouse", "kql"),
            cluster_uri,
            {
                "database": database,
                "table_prefix": table_prefix,
                "batch_size": batch_size,
                "auth": auth,
                **dict(credentials or {}),
            },
            name="kql",
        )
