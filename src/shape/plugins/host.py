"""Plugin host: discovery, API version check, lazy loading, registry, failure isolation.

This is the stable surface that the plugin CLI (P2-03), the built-ins (P2-04) and the plugin
kit (P2-06) build on. Everything is reachable from :class:`PluginHost`; most callers use the
process-wide host from :func:`default_host`.

Lifecycle of a plugin:

1. **Discovery** reads entry-point metadata only (``importlib.metadata``). It imports nothing.
2. **Loading** is lazy: the first :meth:`PluginHost.get` (or :meth:`PluginHost.load_all`)
   imports the entry point's module, checks its ``SHAPE_API`` major version against
   :data:`shape.plugins.api.v1.SHAPE_API`, resolves the entry point to a factory, calls it,
   and checks that the result satisfies the group's Protocol.
3. **Failure isolation:** any exception raised while importing or building a plugin is caught
   and stored on its :class:`PluginRecord` (``status == "error"``). It never propagates out of
   discovery, ``names``, ``records`` or ``load_all``. :meth:`PluginHost.get` raises
   :class:`PluginLoadError` for that one plugin only; :meth:`PluginHost.try_get` returns
   ``None``. A plugin that failed is not retried until :meth:`PluginHost.reload`.

Plugins are trusted, in-process code (D-09): the host checks compatibility, not safety.
"""

from __future__ import annotations

import importlib
import threading
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from importlib import metadata
from typing import Any

from shape.errors import ShapeError
from shape.plugins.api import v1

HOST_API = v1.SHAPE_API
_UNLOADED = "unloaded"
_LOADED = "ok"
_ERROR = "error"


class PluginLoadError(ShapeError):
    """One plugin could not be loaded. Never raised by discovery or listing."""


@dataclass(slots=True)
class PluginRecord:
    """Everything the host knows about one registered plugin.

    ``status`` is ``"unloaded"`` (discovered, not imported), ``"ok"`` or ``"error"``.
    ``error`` holds ``"<ExceptionType>: <message>"`` when ``status == "error"``.
    ``api`` is the plugin module's declared ``SHAPE_API`` once known, else ``None``.
    ``source`` is the distribution name for an entry point, or ``"<registered>"``.
    ``obj`` is the live plugin object once loaded.
    """

    group: str
    name: str
    target: str
    source: str
    status: str = _UNLOADED
    api: str | None = None
    error: str | None = None
    obj: Any = None

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe view (never includes the live object)."""
        return {
            "group": self.group,
            "name": self.name,
            "target": self.target,
            "source": self.source,
            "status": self.status,
            "api": self.api,
            "error": self.error,
        }


def api_major(version: str) -> int:
    """The major number of an ``"M.m"`` API version string; ``ValueError`` if malformed."""
    head = str(version).split(".", 1)[0]
    if not head.isdigit():
        raise ValueError(f"malformed SHAPE_API {version!r} (expected 'MAJOR.MINOR')")
    return int(head)


def check_api(declared: object) -> str:
    """Return ``declared`` if its major matches the host's, else raise ``PluginLoadError``."""
    if not isinstance(declared, str):
        raise PluginLoadError("plugin does not declare SHAPE_API (a string such as '1.0')")
    try:
        major = api_major(declared)
    except ValueError as exc:
        raise PluginLoadError(str(exc)) from None
    if major != api_major(HOST_API):
        raise PluginLoadError(
            f"plugin targets plugin API {declared}, but this Shape provides API {HOST_API}; "
            f"major versions must match"
        )
    return declared


EntryPointsFn = Callable[[], Iterable[metadata.EntryPoint]]


def _installed_entry_points() -> Iterator[metadata.EntryPoint]:
    for group in v1.GROUPS:
        yield from metadata.entry_points(group=group)


class PluginHost:
    """A registry of plugins keyed by ``(group, name)``.

    ``entry_points`` replaces installed-package discovery (used by tests). Discovery runs once,
    on first use; call :meth:`reload` to repeat it.
    """

    def __init__(self, entry_points: EntryPointsFn | None = None) -> None:
        self._entry_points = entry_points or _installed_entry_points
        self._records: dict[tuple[str, str], PluginRecord] = {}
        self._unkeyed: list[PluginRecord] = []
        self._discovered = False
        self._lock = threading.RLock()

    # -- discovery and registration -------------------------------------------------------

    def _discover(self) -> None:
        with self._lock:
            if self._discovered:
                return
            self._discovered = True
            try:
                eps = sorted(self._entry_points(), key=lambda e: (e.group, e.name, e.value))
            except Exception as exc:  # broken metadata must not take the host down
                self._unkeyed.append(
                    PluginRecord("", "<discovery>", "", "<metadata>", _ERROR, None, _describe(exc))
                )
                return
            for ep in eps:
                if ep.group not in v1.GROUPS:
                    continue
                key = (ep.group, ep.name)
                dist = getattr(ep, "dist", None)
                source = (dist.name if dist is not None else None) or "<unknown>"
                if key in self._records:
                    # First (sorted) registration wins; the duplicate is reported, not loaded.
                    self._unkeyed.append(
                        PluginRecord(
                            ep.group,
                            ep.name,
                            ep.value,
                            source,
                            _ERROR,
                            None,
                            f"duplicate name {ep.name!r} in group {ep.group}; "
                            f"already provided by {self._records[key].source}",
                        )
                    )
                    continue
                self._records[key] = PluginRecord(ep.group, ep.name, ep.value, source)

    def register(
        self, group: str, name: str, obj: Any, *, api: str = HOST_API, source: str = "<registered>"
    ) -> PluginRecord:
        """Register a ready object (or zero-argument factory) without an entry point.

        Used for built-ins and tests. The API check and Protocol check still apply, at load.
        Raises ``ValueError`` for an unknown group or a duplicate name.
        """
        if group not in v1.GROUPS:
            raise ValueError(f"unknown plugin group {group!r}")
        self._discover()
        with self._lock:
            if (group, name) in self._records:
                raise ValueError(f"{name!r} is already registered in group {group}")
            rec = PluginRecord(group, name, repr(obj), source)
            rec.obj = _Pending(obj, api)
            self._records[(group, name)] = rec
            return rec

    def reload(self) -> None:
        """Forget everything loaded and discover again (registered objects are dropped)."""
        with self._lock:
            self._records.clear()
            self._unkeyed.clear()
            self._discovered = False

    # -- queries (never import a plugin) --------------------------------------------------

    def records(self, group: str | None = None) -> list[PluginRecord]:
        """Every known plugin, sorted by ``(group, name)``; optionally one group only."""
        self._discover()
        with self._lock:
            every = [*self._records.values(), *self._unkeyed]
            rows = [r for r in every if group in (None, r.group)]
        return sorted(rows, key=lambda r: (r.group, r.name, r.source))

    def names(self, group: str) -> list[str]:
        """Names registered in ``group`` that did not fail discovery."""
        self._discover()
        with self._lock:
            return sorted(n for g, n in self._records if g == group)

    def record(self, group: str, name: str) -> PluginRecord | None:
        self._discover()
        with self._lock:
            return self._records.get((group, name))

    # -- loading --------------------------------------------------------------------------

    def get(self, group: str, name: str) -> Any:
        """The plugin object for ``(group, name)``, loading it on first use.

        Raises ``KeyError`` when nothing is registered under that name, and
        :class:`PluginLoadError` when it is registered but cannot load.
        """
        self._discover()
        with self._lock:
            rec = self._records.get((group, name))
            if rec is None:
                raise KeyError(f"no plugin {name!r} in group {group}")
            if rec.status == _ERROR:
                raise PluginLoadError(f"plugin {group}:{name} failed to load: {rec.error}")
            if rec.status == _LOADED:
                return rec.obj
            self._load(rec)
            if rec.status == _ERROR:
                raise PluginLoadError(f"plugin {group}:{name} failed to load: {rec.error}")
            return rec.obj

    def try_get(self, group: str, name: str) -> Any | None:
        """Like :meth:`get`, but ``None`` instead of ``KeyError`` or ``PluginLoadError``."""
        try:
            return self.get(group, name)
        except (KeyError, PluginLoadError):
            return None

    def load_all(self, group: str | None = None) -> list[PluginRecord]:
        """Try to load every plugin (of ``group``, or all) and return their records.

        Failures are recorded, never raised. This is what ``shape plugins doctor`` runs.
        """
        for rec in self.records(group):
            if rec.status == _UNLOADED:
                with self._lock:
                    if rec.status == _UNLOADED:
                        self._load(rec)
        return self.records(group)

    def _load(self, rec: PluginRecord) -> None:
        try:
            pending = rec.obj if isinstance(rec.obj, _Pending) else None
            if pending is not None:
                rec.api = check_api(pending.api)
                factory = pending.obj
            else:
                ep = metadata.EntryPoint(rec.name, rec.target, rec.group)
                module = importlib.import_module(ep.module)
                rec.api = check_api(getattr(module, "SHAPE_API", None))
                factory = ep.load()
            proto = v1.PROTOCOLS[v1.GROUPS[rec.group]]
            obj = _build(factory, proto)
            if not isinstance(obj, proto):
                raise PluginLoadError(
                    f"object does not implement {proto.__name__} (group {rec.group})"
                )
            rec.obj = obj
            rec.status = _LOADED
            rec.error = None
        except (Exception, SystemExit) as exc:  # a plugin calling sys.exit must not end core
            rec.obj = None
            rec.status = _ERROR
            rec.error = _describe(exc)


@dataclass(slots=True)
class _Pending:
    obj: Any
    api: str


def _build(factory: Any, proto: type) -> Any:
    """A class is instantiated; an object that already satisfies ``proto`` is used as it is;
    any other callable is called with no arguments."""
    if isinstance(factory, type):
        return factory()
    if isinstance(factory, proto):
        return factory
    return factory() if callable(factory) else factory


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


_default: PluginHost | None = None
_default_lock = threading.Lock()


def default_host() -> PluginHost:
    """The process-wide host, created on first use (discovers installed plugins)."""
    global _default
    with _default_lock:
        if _default is None:
            _default = PluginHost()
        return _default


def reset_default_host() -> None:
    """Drop the process-wide host (tests; call after installing or removing a plugin)."""
    global _default
    with _default_lock:
        _default = None
