"""Output targets by URI: which ``shape.sinks`` plugin writes ``abfss://...``, ``mssql://...``.

A target is a URI whose scheme a registered sink lists in its ``schemes`` (``abfss``,
``delta+abfss``, ``mssql``, ``postgresql``, ...). A plain path, a ``file://`` URI and a Windows
drive letter are local files and are not routed here (the file sinks are chosen by ``--format``).
"""

from __future__ import annotations

from typing import Any

from shape.errors import ShapeError
from shape.plugins.schemes import redact, sinks_by_scheme, uri_scheme

LOCAL_SCHEMES = frozenset({"file"})


def scheme_of(target: str) -> str | None:
    """The scheme of a URI target, or ``None`` for a path (``file://`` counts as a path)."""
    scheme = uri_scheme(target)
    return None if scheme in LOCAL_SCHEMES else scheme


def is_remote_target(target: str) -> bool:
    return scheme_of(target) is not None


def sink_names_by_scheme() -> dict[str, str]:
    """Every non-file scheme a registered sink handles, with the name of the first sink."""
    return {
        scheme: names[0]
        for scheme, names in sinks_by_scheme().items()
        if scheme not in LOCAL_SCHEMES
    }


def sink_for_target(target: str) -> tuple[str, Any]:
    """``(name, sink)`` of the sink that writes ``target``; a ``ShapeError`` that lists the
    schemes in use when none does."""
    scheme = scheme_of(target)
    if scheme is None:
        raise ShapeError(f"{target!r} is a local path, not a URI for a sink")
    from shape.plugins.host import default_host

    name = sink_names_by_scheme().get(scheme)
    if name is None:
        known = ", ".join(f"{s}://" for s in sorted(sink_names_by_scheme())) or "none installed"
        raise ShapeError(
            f"no sink writes {scheme}:// targets (installed: {known}). "
            "Databases and cloud stores come with plugins: see `shape plugins list`."
        )
    return name, default_host().get("shape.sinks", name)


__all__ = ["is_remote_target", "redact", "scheme_of", "sink_for_target", "sink_names_by_scheme"]
