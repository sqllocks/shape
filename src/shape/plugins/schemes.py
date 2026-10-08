"""URI schemes for sinks: which sink handles which scheme, and the check a sink makes first.

Every ``shape.sinks`` plugin declares the ``schemes`` it writes to. :func:`require_scheme` is what
a sink calls before it touches anything, so a cloud or database URI fails with a message that says
what the sink does write, which sinks handle which scheme, and where a missing scheme would come
from, instead of an operating-system error (or a directory named ``abfss:``).
"""

from __future__ import annotations

import os
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


def file_uri_path(uri: str, *, windows: bool | None = None) -> str:
    """The file system path a ``file:`` URI names, keeping a Windows drive or UNC host.

    ``windows`` defaults to the running platform.
    """
    if windows is None:
        windows = os.name == "nt"
    rest = str(uri)[len("file:") :]
    host = ""
    if rest.startswith("//"):
        host, slash, tail = rest[2:].partition("/")
        rest = slash + tail
    path = unquote(rest.split("?", 1)[0].split("#", 1)[0])
    if not windows:
        return path
    host = unquote(host)
    if len(host) >= 2 and host[0].isalpha() and host[1] in ":|":
        return host[0] + ":" + host[2:] + path  # file://C:/x or file://C:\x: a drive, not a host
    if host and host.lower() != "localhost":
        return "//" + host + path
    if len(path) > 2 and path[0] == "/" and path[1].isalpha() and path[2] in ":|":
        path = path[1] + ":" + path[3:]  # file:///C:/x names the drive, not a root
    return path


def local_path(uri: str | Path) -> Path:
    """The local path of a plain path or a ``file://`` URI."""
    text = str(uri)
    if uri_scheme(text) == "file" and text.lower().startswith("file:"):
        return Path(file_uri_path(text))
    return Path(uri)


# Query parameters whose value is a credential (case-insensitive): a SAS signature, a password,
# an account or shared-access key, a token or a client secret.
_SECRET_PARAM = re.compile(
    r"(^|[&;])((?:sig|signature|password|passwd|pwd|accountkey|sharedaccesskey|"
    r"sharedaccesssignature|token|access_token|sas_token|client_secret|secret|api_key|apikey)=)"
    r"[^&;]*",
    re.IGNORECASE,
)


def redact(uri: str) -> str:
    """``uri`` without a password in its user information or a credential in its query string
    (``sig``, ``password``, ``AccountKey``, ``SharedAccessKey``, tokens and secrets)."""
    try:
        parts = urlsplit(str(uri))
        if parts.password is not None:
            host = parts.netloc.rpartition("@")[2]
            user = parts.username or ""
            parts = parts._replace(netloc=f"{user}:***@{host}")
        if parts.query:
            parts = parts._replace(query=_SECRET_PARAM.sub(r"\1\2***", parts.query))
        out = parts.geturl()
        return str(uri) if out == urlsplit(str(uri)).geturl() else out
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
    # ``file`` first (what most sinks write to), then the others by name.
    ordered = sorted(out.items(), key=lambda item: (item[0] != "file", item[0]))
    return {scheme: sorted(names) for scheme, names in ordered}


def _describe(schemes: Sequence[str]) -> str:
    schemes = [s.lower() for s in schemes]
    if schemes == ["file"]:
        return "local files"
    others = [s for s in schemes if s != "file"]
    listing = ", ".join(f"{s}://" for s in others)
    if "file" in schemes:
        return f"local files and URIs of scheme {listing}"
    return f"URIs of scheme {listing}"


def _unavailable(scheme: str, handlers: Sequence[str] = ()) -> str:
    kind = _KNOWN.get(scheme)
    if handlers:
        which = " and ".join(handlers)
        what = f"{kind} database sinks" if scheme in _DATABASES else f"{kind or scheme} sinks"
        return f"{what} are provided by the {which} sink: shape generate --to {scheme}://..."
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
        f"({_unavailable(scheme, table.get(scheme, ()))}{sql_note}). Sinks by scheme: {listing}. "
        "A plugin adds a scheme by registering a shape.sinks sink that declares it "
        "(see `shape plugins list`)."
    )


__all__ = [
    "UnsupportedSchemeError",
    "local_path",
    "redact",
    "require_scheme",
    "sinks_by_scheme",
    "uri_scheme",
]
