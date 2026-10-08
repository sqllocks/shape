"""Output targets by URI: which ``shape.sinks`` plugin writes ``abfss://...``, ``mssql://...``.

A target is a URI whose scheme a registered sink lists in its ``schemes`` (``abfss``,
``delta+abfss``, ``mssql``, ``postgresql``, ...). A plain path, a ``file://`` URI and a Windows
drive letter are local files and are not routed here (the file sinks are chosen by ``--format``).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable, Mapping
from typing import IO, Any
from urllib.parse import urlsplit

from shape.errors import ShapeError
from shape.plugins.schemes import redact, sinks_by_scheme, uri_scheme

LOCAL_SCHEMES = frozenset({"file"})
#: Destinations that never leave the machine: ``console``, the ``file`` emit sink, ``memory`` and
#: the folder sinks. A URI on one of these hosts is an emulator or a local service.
LOCAL_NAMES = frozenset({"console", "file", "memory", "parquet"})
#: ``duckdb://`` is a DuckDB file on this machine: its sink refuses a host (W2-10).
LOCAL_URI_SCHEMES = frozenset({"file", "jsonl", "duckdb"})
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
CONFIRM_ENV = "SHAPE_CONFIRM_REMOTE"


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


def is_local_destination(target: str) -> bool:
    """True for a destination that stays on this machine: a path, ``file://``, ``jsonl://``,
    ``duckdb://`` (a local DuckDB file), ``console`` and a URI whose host is ``localhost``,
    ``127.0.0.1`` or ``::1`` (emulators)."""
    text = str(target).strip()
    if text.lower() in LOCAL_NAMES:
        return True
    from shape.plugins.schemes import uri_scheme

    scheme = uri_scheme(text)
    if scheme in LOCAL_URI_SCHEMES:
        return True
    try:
        host = urlsplit(text).hostname
    except ValueError:
        return False
    return host is not None and host.lower() in LOCAL_HOSTS


def nonlocal_destinations(targets: Iterable[str]) -> list[str]:
    """The destinations among ``targets`` that are not :func:`is_local_destination`, in order,
    each once."""
    seen: dict[str, None] = {}
    for target in targets:
        if not is_local_destination(target):
            seen.setdefault(str(target))
    return list(seen)


def scale_sink_destinations(
    sinks: Iterable[str], config: Mapping[str, Mapping[str, Any]] | None = None
) -> list[str]:
    """The ``shape generate --scale-mode`` sinks that write off this machine, as the URI to show (``warehouse://``):
    ``memory`` and ``parquet`` are local; ``lakehouse`` is local when its ``base_path`` is a local
    folder or a loopback URI; ``warehouse``, ``sql_database`` and ``kql`` are not."""
    config = config or {}
    out: list[str] = []
    for name in sinks:
        if name in ("memory", "parquet"):
            continue
        if name == "lakehouse":
            base = str((config.get("lakehouse") or {}).get("base_path") or "")
            if base and is_local_destination(base):
                continue
            out.append(base or "lakehouse://")
        else:
            out.append(f"{name.replace('_', '-')}://")  # a scheme has no underscore
    return out


def confirm_remote_targets(
    targets: Iterable[str],
    *,
    confirm: bool = False,
    dry_run: bool = False,
    stdin: IO[str] | None = None,
    stderr: IO[str] | None = None,
) -> bool:
    """Make sure the user meant to write to every non-local destination in ``targets``.

    Confirmed by ``confirm`` (``--yes`` or ``confirm_remote=True``), by ``SHAPE_CONFIRM_REMOTE=1``
    (any other value does not confirm), or by answering ``y`` at the prompt when stdin and stderr
    are a terminal. A dry run needs none. Otherwise a ``ShapeError`` (exit 2). Called before any
    connection or sign-in. Returns ``True`` once the targets are confirmed (or none is
    non-local), so a caller can pass the answer on and not ask twice."""
    remote = nonlocal_destinations(targets)
    if not remote or dry_run or confirm or os.environ.get(CONFIRM_ENV) == "1":
        return True
    shown = [redact(t) for t in remote]
    stdin = sys.stdin if stdin is None else stdin
    stderr = sys.stderr if stderr is None else stderr
    if _is_tty(stdin) and _is_tty(stderr):
        count = len(shown)
        stderr.write(
            f"Write to {count} non-local target{'s' if count > 1 else ''}: {', '.join(shown)}? "
            "[y/N] "
        )
        stderr.flush()
        if stdin.readline().strip().lower() in ("y", "yes"):
            return True
    noun = "target" if len(shown) == 1 else "targets"
    raise ShapeError(
        f"refusing to write to non-local {noun} {', '.join(shown)} without confirmation; "
        f"pass --yes or set {CONFIRM_ENV}=1"
    )


def _is_tty(stream: IO[str]) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


__all__ = [
    "CONFIRM_ENV",
    "confirm_remote_targets",
    "is_local_destination",
    "is_remote_target",
    "nonlocal_destinations",
    "redact",
    "scale_sink_destinations",
    "scheme_of",
    "sink_for_target",
    "sink_names_by_scheme",
]
