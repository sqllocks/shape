"""Sinks for the scale router: memory, Parquet part files, and the Fabric sinks.

``build_sinks(names, config)`` makes sinks from names and per-sink settings, as the command line
and the bridge give them. Nothing here imports pyarrow or a writer until a sink is used.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from shape.scale.sinks.base import BaseSink, FabricConnectionProfile, Sink, SinkError

SINK_NAMES = ("memory", "parquet", "lakehouse", "warehouse", "sql_database", "kql")

_SECRET_KEYS = ("client_secret", "password", "token", "sas", "secret", "key")
_CONN_SECRET = re.compile(
    r"(?i)\b(pwd|password|accountkey|sharedaccesskey|sharedaccesssignature)\s*=[^;]*"
)


def _check(name: str, cfg: Mapping[str, Any], allowed: Sequence[str]) -> None:
    unknown = sorted(set(cfg) - set(allowed))
    if unknown:
        raise ValueError(
            f"sink {name!r}: unknown setting {', '.join(unknown)}; settings: {', '.join(allowed)}"
        )


def _number(name: str, cfg: dict[str, Any], key: str, kind: type, minimum: float) -> None:
    """``cfg[key]`` as a ``kind`` of at least ``minimum``, when it is set. The command line keeps
    a value that is not an integer as text, so ``"0.5"`` arrives as a string."""
    if cfg.get(key) is None:
        return
    raw = cfg[key]
    try:
        if isinstance(raw, bool):
            raise ValueError
        value = kind(raw)
        if kind is int and isinstance(raw, float) and raw != value:
            raise ValueError
    except (TypeError, ValueError):
        noun = "an integer" if kind is int else "a number"
        raise ValueError(f"sink {name!r}: {key} must be {noun}, got {raw!r}") from None
    if not (value > minimum if kind is float else value >= minimum):  # also refuses NaN
        bound = f"more than {minimum:g}" if kind is float else f"at least {minimum:g}"
        raise ValueError(f"sink {name!r}: {key} must be {bound}, got {raw!r}")
    cfg[key] = value


def build_sink(
    name: str,
    cfg: Mapping[str, Any] | None = None,
    *,
    chunk_rows: int = 500_000,
    resume: bool = False,
    auth: Mapping[str, Any] | None = None,
    resolve: bool = True,
) -> Sink:
    """One sink by name with its settings (a mapping; see each sink's class).

    ``auth`` is the sign-in (``--auth`` and friends, see ``shape.cli.auth``): for the Fabric sinks
    it becomes the writer's credential, or a SQL login. ``resolve=False`` only checks names and
    settings: no credential reference is resolved and no credential is built (validation
    before a job exists must not read a vault or start a sign-in)."""
    settings = dict(cfg or {})
    if name == "memory":
        from shape.scale.sinks.memory import MemorySink

        _check(name, settings, ("max_memory_gb",))
        _number(name, settings, "max_memory_gb", float, 0)
        return MemorySink(**settings)
    if name == "parquet":
        from shape.scale.sinks.parquet import ParquetSink

        _check(name, settings, ("output_dir", "chunk_rows", "writer_threads"))
        _number(name, settings, "chunk_rows", int, 1)
        _number(name, settings, "writer_threads", int, 1)
        out = settings.pop("output_dir", None)
        if not out:
            raise ValueError("sink 'parquet' needs output_dir")
        settings.setdefault("chunk_rows", chunk_rows)
        return ParquetSink(out, resume=resume, **settings)
    from shape.scale.sinks import fabric

    if name == "lakehouse":
        _check(name, settings, ("base_path", "format"))
        return fabric.LakehouseSink(
            settings.pop("base_path", ""),
            **settings,
            writer_options=fabric.auth_options(auth) if resolve else None,
        )
    if name == "warehouse":
        _check(
            name,
            settings,
            (
                "connection_string",
                "staging_path",
                "schema_name",
                "write_mode",
                "chunk_size",
            ),
        )
        conn, opts = fabric.connection_and_auth(
            settings.pop("connection_string", ""), auth, resolve
        )
        return fabric.WarehouseSink(
            conn, settings.pop("staging_path", ""), **settings, writer_options=opts
        )
    if name == "sql_database":
        _check(
            name,
            settings,
            ("connection_string", "schema_name", "write_mode", "batch_size"),
        )
        conn, opts = fabric.connection_and_auth(
            settings.pop("connection_string", ""), auth, resolve
        )
        return fabric.SqlDatabaseSink(conn, **settings, writer_options=opts)
    if name == "kql":
        _check(name, settings, ("cluster_uri", "database", "table_prefix", "write_mode"))
        return fabric.KqlSink(
            settings.pop("cluster_uri", ""),
            settings.pop("database", ""),
            **settings,
            writer_options=fabric.auth_options(auth) if resolve else None,
        )
    raise ValueError(f"unknown sink {name!r}; the sinks are: {', '.join(SINK_NAMES)}")


def build_sinks(
    names: Sequence[str],
    config: Mapping[str, Mapping[str, Any]] | None = None,
    *,
    chunk_rows: int = 500_000,
    resume: bool = False,
    auth: Mapping[str, Any] | None = None,
    resolve: bool = True,
) -> list[Sink]:
    """Sinks for ``names``, each with ``config[name]``."""
    config = config or {}
    unused = sorted(set(config) - set(names))
    if unused:
        raise ValueError(f"settings for sinks not in use: {', '.join(unused)}")
    return [
        build_sink(
            n, config.get(n), chunk_rows=chunk_rows, resume=resume, auth=auth, resolve=resolve
        )
        for n in names
    ]


def redact(config: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """``config`` with every secret-looking setting, and the password of a connection string,
    masked: what a job record may keep."""
    from shape.scale.jobs import MASK

    def one(key: str, value: Any) -> Any:
        if any(s in key.lower() for s in _SECRET_KEYS) and value:
            return MASK
        if isinstance(value, str):
            return _CONN_SECRET.sub(lambda m: m.group(0).split("=", 1)[0] + "=" + MASK, value)
        return value

    return {sink: {k: one(k, v) for k, v in cfg.items()} for sink, cfg in config.items()}


__all__ = [
    "SINK_NAMES",
    "BaseSink",
    "FabricConnectionProfile",
    "Sink",
    "SinkError",
    "build_sink",
    "build_sinks",
    "redact",
]
