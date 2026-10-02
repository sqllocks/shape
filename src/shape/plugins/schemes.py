"""URI schemes for sinks: which sink handles which scheme, and the check a sink makes first.

Every ``shape.sinks`` plugin declares the ``schemes`` it writes to. :func:`require_scheme` is what
a sink calls before it touches anything, so a cloud or database URI fails with a message that says
what the sink does write, which sinks handle which scheme, and where a missing scheme would come
from, instead of an operating-system error (or a directory named ``abfss:``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from shape.errors import ShapeCapabilityError

_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*):(?=/)")

# What a URI scheme stands for, for the message. Nothing here is a promise that a plugin exists:
# the text says "not available" until a ``shape.sinks`` plugin declares the scheme.
_KNOWN: dict[str, str] = {
    "abfss": "OneLake and ADLS Gen2",
    "abfs": "OneLake and ADLS Gen2",
    "az": "Azure Blob Storage",
    "wasbs": "Azure Blob Storage",
    "wasb": "Azure Blob Storage",
    "http": "web",
    "https": "Azure Blob Storage and web",
    "s3": "Amazon S3",
    "gs": "Google Cloud Storage",
    "mssql": "SQL Server",
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "mysql": "MySQL",
    "sqlite": "SQLite",
    "kafka": "Kafka",
    "eventhubs": "Event Hubs",
}
_DATABASES = frozenset({"mssql", "postgres", "postgresql", "mysql", "sqlite"})


class UnsupportedSchemeError(ShapeCapabilityError, ValueError):
    """A sink was given a URI whose scheme it does not write to."""


def uri_scheme(uri: str) -> str:
    """The lower-cased scheme of ``uri``; ``"file"`` for a path, a ``file:`` URI or a Windows
    drive path such as ``C:\\data`` (a one-letter scheme is a drive)."""
    text = str(uri)
    match = _SCHEME.match(text)
    if match is None and "://" in text:
        match = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*):", text)
    if match is None:
        return "file"
    scheme = match.group(1).lower()
    return "file" if len(scheme) == 1 else scheme


def local_path(uri: str | Path) -> Path:
    """The local path of a plain path or a ``file://`` URI."""
    text = str(uri)
    if uri_scheme(text) == "file" and text.lower().startswith("file:"):
        return Path(unquote(urlsplit(text).path))
    return Path(uri)


def redact(uri: str) -> str:
    """``uri`` without a password in its user information."""
    try:
        parts = urlsplit(str(uri))
        if parts.password is None:
            return str(uri)
        host = parts.netloc.rpartition("@")[2]
        user = parts.username or ""
        return parts._replace(netloc=f"{user}:***@{host}").geturl()
    except ValueError:
        return str(uri)


def sinks_by_scheme(host: Any | None = None) -> dict[str, list[str]]:
    """``{scheme: [sink name, ...]}`` for every sink that loads (sinks that fail to load are
    left out; ``shape plugins doctor`` reports them)."""
    if host is None:
        from shape.plugins.host import default_host

        host = default_host()
    out: dict[str, list[str]] = {}
    for name in host.names("shape.sinks"):
        sink = host.try_get("shape.sinks", name)
        for scheme in getattr(sink, "schemes", ()) if sink is not None else ():
            out.setdefault(str(scheme).lower(), []).append(name)
    return {scheme: sorted(names) for scheme, names in sorted(out.items())}


def _describe(schemes: Sequence[str]) -> str:
    schemes = [s.lower() for s in schemes]
    if schemes == ["file"]:
        return "local files"
    return "URIs of scheme " + ", ".join(f"{s}://" for s in schemes)


def _unavailable(scheme: str) -> str:
    kind = _KNOWN.get(scheme)
    if kind is None:
        return f"no installed sink writes {scheme}:// URIs"
    if scheme in _DATABASES:
        return f"{kind} database sinks are not available yet"
    return f"{kind} sinks are not available yet"


def require_scheme(sink: Any, uri: str, *, host: Any | None = None) -> None:
    """Raise :class:`UnsupportedSchemeError` unless ``uri``'s scheme is one ``sink`` declares.

    The message names the sink, what it writes to, the URI (a password is hidden), why the scheme
    is not handled, and which installed sinks handle which scheme.
    """
    declared: Iterable[str] = getattr(sink, "schemes", ("file",))
    schemes = [str(s).lower() for s in declared]
    scheme = uri_scheme(uri)
    if scheme in schemes:
        return
    name = getattr(sink, "name", type(sink).__name__)
    table = sinks_by_scheme(host)
    listing = "; ".join(f"{s}: {', '.join(names)}" for s, names in table.items()) or "none"
    sql_note = ""
    if scheme in _DATABASES:
        sql_note = " (the sql sink writes INSERT scripts to a file that you can run against it)"
    raise UnsupportedSchemeError(
        f"the {name} sink writes only to {_describe(schemes)}; got {redact(uri)} "
        f"({_unavailable(scheme)}{sql_note}). Sinks by scheme: {listing}. A plugin adds a scheme "
        "by registering a shape.sinks sink that declares it (see `shape plugins list`)."
    )


__all__ = [
    "UnsupportedSchemeError",
    "local_path",
    "redact",
    "require_scheme",
    "sinks_by_scheme",
    "uri_scheme",
]
