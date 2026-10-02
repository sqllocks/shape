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
from pathlib import Path
from urllib.parse import unquote, urlsplit

from shape.errors import ShapeError

ONELAKE_HOST = "onelake.dfs.fabric.microsoft.com"
SECTIONS = ("Files", "Tables")
_GUID = re.compile(r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
_BAD_SEGMENT = re.compile(r"[\x00-\x1f\\?#]")
_ITEM_TYPES = (".Lakehouse", ".Warehouse", ".SQLDatabase", ".KQLDatabase", ".Eventhouse")


def segment(name: str, what: str = "name") -> str:
    """``name`` as one path segment, or a :class:`ShapeError`."""
    if not isinstance(name, str) or not name or name in (".", "..") or "/" in name:
        raise ShapeError(f"not a usable {what} for a path: {name!r}")
    if _BAD_SEGMENT.search(name):
        raise ShapeError(f"a {what} cannot contain control characters, backslashes, ? or #")
    return name


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
        return OneLakePath(self.workspace, self.item, "/".join([*self.path.split("/"), *segs]).strip("/"))

    def abfss(self) -> str:
        tail = f"/{self.path}" if self.path else ""
        return f"abfss://{self.workspace}@{ONELAKE_HOST}/{self.item}{tail}"

    def https(self) -> str:
        tail = f"/{self.path}" if self.path else ""
        return f"https://{ONELAKE_HOST}/{self.workspace}/{self.item}{tail}"

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
        if not workspace or host != ONELAKE_HOST:
            raise ShapeError(
                f"not a OneLake URI: {uri!r} (abfss://<workspace>@{ONELAKE_HOST}/<item>/...)"
            )
        segs = [unquote(s) for s in parts.path.split("/") if s]
        if not segs:
            raise ShapeError(f"a OneLake URI needs an item after the host: {uri!r}")
        return OneLakePath(workspace, segs[0], "/".join(segs[1:]))
    if parts.scheme == "onelake":
        segs = [unquote(s) for s in parts.path.split("/") if s]
        if not parts.netloc or not segs:
            raise ShapeError(f"not a OneLake URI: {uri!r} (onelake://<workspace>/<lakehouse>/...)")
        return OneLakePath(parts.netloc, item_name(segs[0]), "/".join(segs[1:]))
    raise ShapeError(f"not a OneLake URI: {uri!r}")


def to_abfss(uri: str) -> str:
    """``uri`` (``onelake://`` or ``abfss://``) as the ``abfss://`` URI the core source reads."""
    return parse(uri).abfss()


def join(base: str, *parts: str) -> str:
    """``base`` (a local directory or a URI) with ``parts`` appended; every part is one checked
    segment (it may contain ``/`` between segments)."""
    segs = [segment(s, "path segment") for part in parts for s in str(part).split("/") if s]
    if is_remote(base):
        return "/".join([base.rstrip("/"), *segs])
    return str(Path(base, *segs))


def parent(path: str) -> str:
    if is_remote(path):
        head, _, _ = path.rstrip("/").rpartition("/")
        return head
    return str(Path(path).parent)


def _dt(value: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ShapeError(f"a landing-zone date is YYYY-MM-DD, got {value!r}")
    return value


def landing_zone(
    base: str, domain: str, entity: str, dt: str, hour: str | int | None = None
) -> str:
    """The partition folder ``landing/<domain>/<entity>/dt=<dt>[/hour=<HH>]``."""
    path = join(base, "landing", domain, entity, f"dt={_dt(dt)}")
    if hour is not None:
        h = int(hour)
        if not 0 <= h <= 23:
            raise ShapeError(f"an hour is 0 to 23, got {hour!r}")
        path = join(path, f"hour={h:02d}")
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
