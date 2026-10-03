"""OneLake paths: parsing, building and the landing-zone layout.

A Fabric item's files live at ``abfss://<workspace>@onelake.dfs.fabric.microsoft.com/<item>/...``
(``<item>`` is a GUID, or ``<name>.Lakehouse``). Shape also accepts the short form
``onelake://<workspace>/<lakehouse>/<Files|Tables>/<path>``, where ``<lakehouse>`` is a name, a
GUID or ``<name>.Lakehouse``. Reading and listing such paths is the core ``abfss://`` source
(``adlfs``); this module only names places.

``COPY INTO`` takes ``https://onelake.dfs.fabric.microsoft.com/<workspace>/<item>/...``, not
``abfss://``: :meth:`OneLakePath.https`.

Landing-zone layout (the folders other Fabric tools expect)::

    <base>/landing/<domain>/<entity>/dt=YYYY-MM-DD[/hour=HH]/part-0001.<ext>
    <base>/landing/<domain>/<entity>/_control/manifest_<dt>.json
    <base>/landing/<domain>/<entity>/_control/_SUCCESS_<dt>
    <base>/quarantine/<domain>/<run_id>/

``<base>`` is a local directory or an ``abfss://`` / ``onelake://`` URI of a ``Files`` folder.
Every name that becomes a path segment is checked: no ``..``, separators or control characters.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from pathlib import PureWindowsPath
from urllib.parse import quote, unquote, urlsplit

from shape.errors import ShapeError

ONELAKE_HOST = "onelake.dfs.fabric.microsoft.com"
SECTIONS = ("Files", "Tables")
_GUID = re.compile(r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
_BAD_SEGMENT = re.compile(r"[\x00-\x1f\\?#]")
# Characters a URL path holds as they are (RFC 3986 sub-delims, ':', '@', and the glob
# brackets): '*', '[' and ']' stay readable for the core source's globbing. '%', spaces, '#',
# '?' and non-ASCII are encoded.
_PATH_SAFE = "!$&'()*+,;=:@[]~"
_ITEM_TYPES = (".Lakehouse", ".Warehouse", ".SQLDatabase", ".KQLDatabase", ".Eventhouse")


def segment(name: str, what: str = "name") -> str:
    """``name`` as one path segment, or a :class:`ShapeError`."""
    if not isinstance(name, str) or not name or name in (".", "..") or "/" in name:
        raise ShapeError(f"not a usable {what} for a path: {name!r}")
    if _BAD_SEGMENT.search(name):
        raise ShapeError(f"a {what} cannot contain control characters, backslashes, ? or #")
    return name


def path_segments(path: str, uri: str) -> list[str]:
    """The decoded segments of a URI's ``path``; a ``.`` or ``..`` (also written ``%2e%2e``)
    would step out of the item, so it is a :class:`ShapeError`."""
    segs = [unquote(s) for s in path.split("/") if s]
    if any(s in (".", "..") for s in segs):
        raise ShapeError(f"a path cannot contain '.' or '..' segments: {uri!r}")
    return segs


def is_remote(path: str) -> bool:
    return path.startswith(("abfss://", "abfs://", "onelake://"))


def item_name(item: str) -> str:
    """``item`` with its item type: a GUID and ``name.Lakehouse`` stay, a bare name gets
    ``.Lakehouse``."""
    segment(item, "item")
    if _GUID.match(item) or item.endswith(_ITEM_TYPES):
        return item
    return f"{item}.Lakehouse"


@dataclass(frozen=True, slots=True)
class OneLakePath:
    """A place in OneLake: workspace, item and the path inside the item."""

    workspace: str
    item: str
    path: str = ""  # inside the item, no leading or trailing slash, e.g. "Files/landing"

    def join(self, *parts: str) -> OneLakePath:
        segs = [segment(p, "path segment") for part in parts for p in part.split("/") if p]
        return OneLakePath(
            self.workspace, self.item, "/".join([*self.path.split("/"), *segs]).strip("/")
        )

    def _tail(self) -> str:
        """The item and path, each segment percent-encoded once (readers decode them once)."""
        segs = [self.item, *(s for s in self.path.split("/") if s)]
        return "/".join(quote(s, safe=_PATH_SAFE) for s in segs)

    def abfss(self) -> str:
        # The workspace is the URI's user part: it is checked (no '@', '/' or ':'), not encoded,
        # because the abfss:// readers take that part as it is written.
        return f"abfss://{self.workspace}@{ONELAKE_HOST}/{self._tail()}"

    def https(self) -> str:
        return f"https://{ONELAKE_HOST}/{quote(self.workspace, safe='')}/{self._tail()}"

    @property
    def section(self) -> str | None:
        head = self.path.split("/", 1)[0]
        return head if head in SECTIONS else None

    def __str__(self) -> str:
        return self.abfss()


def parse(uri: str) -> OneLakePath:
    """The :class:`OneLakePath` of an ``abfss://...onelake...`` or ``onelake://`` URI."""
    parts = urlsplit(uri)
    if parts.scheme in ("abfss", "abfs"):
        workspace, _, host = parts.netloc.partition("@")
        workspace = _workspace(workspace) if workspace else workspace
        if not workspace or host != ONELAKE_HOST:
            raise ShapeError(
                f"not a OneLake URI: {uri!r} (abfss://<workspace>@{ONELAKE_HOST}/<item>/...)"
            )
        segs = path_segments(parts.path, uri)
        if not segs:
            raise ShapeError(f"a OneLake URI needs an item after the host: {uri!r}")
        return OneLakePath(workspace, segs[0], "/".join(segs[1:]))
    if parts.scheme == "onelake":
        segs = path_segments(parts.path, uri)
        if not parts.netloc or not segs:
            raise ShapeError(f"not a OneLake URI: {uri!r} (onelake://<workspace>/<lakehouse>/...)")
        return OneLakePath(_workspace(parts.netloc), item_name(segs[0]), "/".join(segs[1:]))
    raise ShapeError(f"not a OneLake URI: {uri!r}")


def _workspace(raw: str) -> str:
    """A URI's workspace part, decoded and checked: it becomes the user part of an abfss:// URI,
    where '@', '/' and ':' would change which host or path is meant."""
    name = segment(unquote(raw), "workspace")
    if any(ch in name for ch in "@:"):
        raise ShapeError(f"a workspace name cannot contain '@' or ':': {name!r}")
    return name


def to_abfss(uri: str) -> str:
    """``uri`` (``onelake://`` or ``abfss://``) as the ``abfss://`` URI the core source reads."""
    return parse(uri).abfss()


def join(base: str, *parts: str) -> str:
    """``base`` (a local directory or a URI) with ``parts`` appended; every part is one checked
    segment (it may contain ``/`` between segments)."""
    if any(not str(part) for part in parts):
        raise ShapeError("a path part cannot be empty")
    segs = [segment(s, "path segment") for part in parts for s in str(part).split("/") if s]
    if is_remote(base):
        return "/".join([base.rstrip("/"), *segs])
    if _is_windows_style(base):
        return str(PureWindowsPath(base, *segs))
    return posixpath.join(base, *segs)


def parent(path: str) -> str:
    if is_remote(path):
        head, _, _ = path.rstrip("/").rpartition("/")
        return head
    if _is_windows_style(path):
        return str(PureWindowsPath(path).parent)
    return posixpath.dirname(path.rstrip("/")) or ("/" if path.startswith("/") else ".")


def _is_windows_style(path: str) -> bool:
    """A drive letter or a backslash; every other location uses ``/`` on every host OS (OneLake
    and ABFS paths always do, and a ``/`` path must not be rewritten with ``\\`` on Windows)."""
    return "\\" in path or bool(PureWindowsPath(path).drive)


def _dt(value: str) -> str:
    """A calendar date written ``YYYY-MM-DD`` in ASCII digits."""
    import datetime

    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ShapeError(f"a landing-zone date is YYYY-MM-DD, got {value!r}")
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        raise ShapeError(f"a landing-zone date must be a real date, got {value!r}") from None
    return value


def _hour(value: str | int) -> int:
    """An hour 0 to 23, given as an int or as ASCII digits."""
    if isinstance(value, bool) or not (
        isinstance(value, int) or (isinstance(value, str) and re.fullmatch(r"[0-9]{1,2}", value))
    ):
        raise ShapeError(f"a landing-zone hour is a whole number 0 to 23, got {value!r}")
    h = int(value)
    if not 0 <= h <= 23:
        raise ShapeError(f"an hour is 0 to 23, got {value!r}")
    return h


def landing_zone(
    base: str, domain: str, entity: str, dt: str, hour: str | int | None = None
) -> str:
    """The partition folder ``landing/<domain>/<entity>/dt=<dt>[/hour=<HH>]``."""
    path = join(base, "landing", domain, entity, f"dt={_dt(dt)}")
    if hour is not None:
        path = join(path, f"hour={_hour(hour):02d}")
    return path


def quarantine(base: str, domain: str, run_id: str) -> str:
    return join(base, "quarantine", domain, run_id)


def control(base: str, domain: str, entity: str) -> str:
    return join(base, "landing", domain, entity, "_control")


def manifest(base: str, domain: str, entity: str, dt: str) -> str:
    return join(control(base, domain, entity), f"manifest_{_dt(dt)}.json")


def done_flag(base: str, domain: str, entity: str, dt: str) -> str:
    return join(control(base, domain, entity), f"_SUCCESS_{_dt(dt)}")


def tables_folder(base: str, table: str) -> str:
    """``<lakehouse>/Tables/<table>``: the sibling of the ``Files`` folder ``base`` names."""
    segment(table, "table")
    root = posixpath.normpath(base.replace("\\", "/")) if not is_remote(base) else base.rstrip("/")
    head, _, last = root.rpartition("/")
    if last != "Files":
        raise ShapeError(f"{base!r} is not a lakehouse Files folder (it must end in /Files)")
    return join(head, "Tables", table)
