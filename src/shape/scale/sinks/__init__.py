"""Sinks for the scale router: memory, Parquet part files, and the Fabric sinks.

``build_sinks(names, config)`` makes sinks from names and per-sink settings, as the command line
and the bridge give them. Nothing here imports pyarrow or a writer until a sink is used.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from shape.scale.sinks.base import BaseSink, Sink, SinkError

SINK_NAMES = ("memory", "parquet", "lakehouse", "warehouse", "sql_database", "kql")

_SECRET_KEYS = ("client_secret", "password", "token", "sas", "secret", "key")
_CONN_SECRET = re.compile(r"(?i)\b(pwd|password|accountkey|sharedaccesskey|sharedaccesssignature)\s*=[^;]*")


def _check(name: str, cfg: Mapping[str, Any], allowed: Sequence[str]) -> None:
    unknown = sorted(set(cfg) - set(allowed))
    if unknown:
        raise ValueError(
            f"sink {name!r}: unknown setting {', '.join(unknown)}; settings: {', '.join(allowed)}"
        )


def build_sink(
    name: str, cfg: Mapping[str, Any] | None = None, *, chunk_rows: int = 500_000, resume: bool = False
) -> Sink:
    """One sink by name with its settings (a mapping; see each sink's class)."""
    settings = dict(cfg or {})
    if name == "memory":
        from shape.scale.sinks.memory import MemorySink

        _check(name, settings, ("max_memory_gb",))
        return MemorySink(**settings)
    if name == "parquet":
        from shape.scale.sinks.parquet import ParquetSink

        _check(name, settings, ("output_dir", "chunk_rows", "writer_threads"))
        out = settings.pop("output_dir", None)
        if not out:
            raise ValueError("sink 'parquet' needs output_dir")
        settings.setdefault("chunk_rows", chunk_rows)
        return ParquetSink(out, resume=resume, **settings)
    from shape.scale.sinks import fabric

    if name == "lakehouse":
        _check(name, settings, ("base_path", "format"))
        return fabric.LakehouseSink(settings.pop("base_path", ""), **settings)
    if name == "warehouse":
        _check(
            name,
            settings,
            ("connection_string", "staging_path", "schema_name", "auth", "chunk_size"),
        )
        return fabric.WarehouseSink(
            settings.pop("connection_string", ""), settings.pop("staging_path", ""), **settings
        )
    if name == "sql_database":
        _check(
            name,
            settings,
            (
                "connection_string",
                "schema_name",
                "write_mode",
                "batch_size",
                "auth",
                "staging_path",
            ),
        )
        return fabric.SqlDatabaseSink(settings.pop("connection_string", ""), **settings)
    if name == "kql":
        _check(name, settings, ("cluster_uri", "database", "table_prefix", "batch_size", "auth"))
        return fabric.KqlSink(settings.pop("cluster_uri", ""), settings.pop("database", ""), **settings)
    raise ValueError(f"unknown sink {name!r}; the sinks are: {', '.join(SINK_NAMES)}")


def build_sinks(
    names: Sequence[str],
    config: Mapping[str, Mapping[str, Any]] | None = None,
    *,
    chunk_rows: int = 500_000,
    resume: bool = False,
) -> list[Sink]:
    """Sinks for ``names``, each with ``config[name]``."""
    config = config or {}
    unused = sorted(set(config) - set(names))
    if unused:
        raise ValueError(f"settings for sinks not in use: {', '.join(unused)}")
    return [build_sink(n, config.get(n), chunk_rows=chunk_rows, resume=resume) for n in names]


def redact(config: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """``config`` with the value of every secret-looking setting (and the password of a connection string) masked, for a job record."""
    from shape.scale.jobs import MASK

    def one(key: str, value: Any) -> Any:
        if any(s in key.lower() for s in _SECRET_KEYS) and value:
            return MASK
        if isinstance(value, str):
            return _CONN_SECRET.sub(lambda m: m.group(0).split("=", 1)[0] + "=" + MASK, value)
        return value

    return {sink: {k: one(k, v) for k, v in cfg.items()} for sink, cfg in config.items()}


__all__ = ["SINK_NAMES", "BaseSink", "Sink", "SinkError", "build_sink", "build_sinks", "redact"]
