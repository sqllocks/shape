"""The remote operations a demo needs behind a connection profile, through ``shape-fabric``.

:class:`FabricServices` drops a table, removes files and checks that a target answers. It builds
the sign-in from the profile's auth method (``shape_fabric.auth``) and loads the plugin when the
first operation runs, so a demo that touches no remote target needs nothing installed. The
seams (``connect``, ``transport``, ``filesystem``) are the plugin's; tests pass recordings.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from shape.demo.connections import ConnectionProfile


def _plugin(name: str) -> Any:
    try:
        return importlib.import_module(f"shape_fabric.{name}")
    except ImportError as exc:
        raise ImportError(
            "this target needs the shape-fabric plugin: pip install sqllocks-shape-fabric"
        ) from exc


def onelake_base(profile: ConnectionProfile) -> str:
    """The OneLake folder a demo writes into: ``onelake://<workspace>/<lakehouse>/Files``."""
    return f"onelake://{profile.workspace_id}/{profile.lakehouse_id}/Files"


def eventhouse_uri(profile: ConnectionProfile) -> str:
    """``eventhouse://<host>/<database>`` for the profile's Eventhouse."""
    raw = profile.eventhouse_uri
    parts = urlsplit(raw if "://" in raw else f"//{raw}")
    query = "?tls=false" if parts.scheme == "http" else ""
    return f"eventhouse://{parts.netloc}/{profile.eventhouse_database}{query}"


class FabricServices:
    def __init__(
        self,
        profile: ConnectionProfile,
        *,
        connect: Callable[..., Any] | None = None,
        transport: Any = None,
        filesystem: Any = None,
    ) -> None:
        self.profile = profile
        self._connect = connect
        self._transport = transport
        self._filesystem = filesystem

    # ---- sign-in ----------------------------------------------------------------------------

    def _connection(self, target: str) -> tuple[str, Any]:
        """The resolved connection string of a SQL target and the credential to connect with."""
        from shape.scale.sinks.fabric import connection_and_auth

        raw = (
            self.profile.warehouse_conn_str
            if target == "warehouse"
            else self.profile.sql_db_conn_str
        )
        if not raw:
            raise ValueError(f"the connection profile has no {target} connection string")
        conn, options = connection_and_auth(raw, self.profile.auth_settings(), True)
        return conn, (options or {}).get("credential")

    def _credential(self) -> Any:
        from shape.scale.sinks.fabric import auth_options

        options = auth_options(self.profile.auth_settings()) or {}
        return options.get("credential")

    # ---- remove ------------------------------------------------------------------------------

    def drop_sql_table(self, target: str, schema_name: str, table: str) -> None:
        conn, credential = self._connection(target)
        _plugin("targets").drop_sql_table(
            conn, schema_name, table, credential=credential, connect=self._connect
        )

    def drop_kql_table(self, table: str) -> None:
        if not (self.profile.eventhouse_uri and self.profile.eventhouse_database):
            raise ValueError("the connection profile needs eventhouse_uri and eventhouse_database")
        _plugin("targets").drop_kql_table(
            eventhouse_uri(self.profile),
            table,
            credential=self._credential(),
            transport=self._transport,
        )

    def remove_files(self, path: str) -> None:
        _plugin("targets").remove_files(
            path, credential=self._credential(), filesystem=self._filesystem
        )

    # ---- check -------------------------------------------------------------------------------

    def check(self, target: str) -> str:
        """One cheap round trip to ``target``; returns a short message or raises."""
        targets = _plugin("targets")
        profile = self.profile
        if target in ("warehouse", "sql_db"):
            conn, credential = self._connection(target)
            return str(targets.check_sql(conn, credential=credential, connect=self._connect))
        if target == "eventhouse":
            return str(
                targets.check_kql(
                    eventhouse_uri(profile),
                    credential=self._credential(),
                    transport=self._transport,
                )
            )
        if target == "lakehouse":
            return str(
                targets.check_files(
                    onelake_base(profile),
                    credential=self._credential(),
                    filesystem=self._filesystem,
                )
            )
        raise ValueError(f"unknown target {target!r}")
