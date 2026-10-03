"""State and compatibility policy, in code (``docs/specs/STATE_AND_COMPATIBILITY.md``).

Every persisted file declares ``format`` (its kind) and an integer ``version`` under the same two
key names, plus ``shape_version`` (the Shape release that wrote it) and ``min_shape_version`` (the
first release that reads this version). This module holds the table of kinds, the reading rules
that follow from the policy, and the few serialization helpers every writer shares:

* :func:`stamp` writes the declaration; :func:`declared_version` reads it, accepting the key names
  older files used (``format_version``, ``schema_version``, ``pack_version``) or none at all;
* :func:`check_readable` is the one place a reader decides: a newer version than this release
  supports fails with the minimum Shape release that reads it, a deprecated version warns (strict
  mode: fails);
* :func:`strict_formats` / ``SHAPE_STRICT_FORMATS=1`` is the strict reader: deprecated versions,
  legacy key names and unknown fields are errors;
* :func:`utc_iso`, :func:`parse_utc_iso` and :func:`json_default` fix how dates and decimals are
  serialized (UTC ISO 8601, decimals as strings, nothing locale dependent).
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import re
import warnings
from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import PurePath
from typing import Any

from shape.errors import ShapeError

FORMAT_KEY = "format"
VERSION_KEY = "version"
WRITER_KEY = "shape_version"
MINIMUM_KEY = "min_shape_version"
BOOKKEEPING_KEYS = (FORMAT_KEY, VERSION_KEY, WRITER_KEY, MINIMUM_KEY)

STRICT_ENV = "SHAPE_STRICT_FORMATS"


class FormatError(ShapeError, ValueError):
    """A persisted file does not follow the policy: no or a malformed version, a conflict between
    version keys, another kind, or (in strict mode) an old key name, an unknown field or a
    deprecated version."""


class UnsupportedVersionError(FormatError):
    """The file declares a version newer than this release reads. ``min_shape_version`` is the
    first release that reads it (``None`` when the file does not say)."""

    def __init__(
        self,
        message: str,
        *,
        kind: str,
        found: int,
        supported: int,
        min_shape_version: str | None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.found = found
        self.supported = supported
        self.min_shape_version = min_shape_version


class FormatDeprecationWarning(DeprecationWarning):
    """A file in a deprecated format version was read (strict mode: an error instead)."""


@dataclass(frozen=True)
class Deprecation:
    """A format version that a future major release will stop reading directly."""

    since: str  # the release that announced it
    removed_in: str  # the major release that drops direct reading
    migrate_with: str  # the offline path that still reads it, e.g. ``shape migrate``


@dataclass(frozen=True)
class Kind:
    """One kind of persisted file.

    ``legacy_version_keys`` are the key names older files used for the version (still read, and
    still written beside ``version`` in the 1.x series). ``implicit_version`` is the version of a
    file that declares none (an older writer that never did). ``unified_in_body`` is false when the
    document's bytes are content-addressed (the model inside a ``.shape`` artifact): its version
    stays under its old key and the unified keys live in the container's manifest instead.
    """

    name: str
    label: str
    format: str
    current: int
    first_release: Mapping[int, str]
    legacy_version_keys: tuple[str, ...] = ()
    legacy_formats: tuple[str, ...] = ()
    implicit_version: int | None = None
    unified_in_body: bool = True
    deprecated: Mapping[int, Deprecation] = field(default_factory=dict)


_FIRST = "0.9.0"


def _kind(
    name: str,
    label: str,
    format: str,
    current: int = 1,
    **kw: Any,
) -> Kind:
    return Kind(
        name=name,
        label=label,
        format=format,
        current=current,
        first_release={v: _FIRST for v in range(1, current + 1)},
        **kw,
    )


KINDS: dict[str, Kind] = {
    k.name: k
    for k in (
        _kind("artifact", "Shape artifact", "shape", 2, legacy_version_keys=("format_version",)),
        _kind(
            "profile-artifact",
            "Shape profile artifact",
            "shape",
            1,
            legacy_version_keys=("format_version",),
        ),
        _kind(
            "model",
            "Shape model",
            "shape-model",
            2,
            legacy_version_keys=("schema_version",),
            unified_in_body=False,
        ),
        _kind(
            "safe-profile",
            "safe profile",
            "shape-safe-profile",
            legacy_version_keys=("schema_version",),
            implicit_version=1,
        ),
        _kind(
            "generation-schema",
            "generation schema",
            "shape-generation-schema",
            legacy_version_keys=("schema_version",),
        ),
        _kind("generation-spec", "generation spec", "shape-generation-spec", implicit_version=1),
        _kind(
            "scenario-pack",
            "scenario pack",
            "shape-scenario-pack",
            legacy_version_keys=("pack_version",),
            implicit_version=1,
        ),
        _kind("registry-layout", "registry layout", "shape-registry", implicit_version=1),
        _kind(
            "profile-registry-layout",
            "profile registry layout",
            "shape-profile-registry",
            implicit_version=1,
        ),
        _kind("run-manifest", "run manifest", "shape-run-manifest", implicit_version=1),
        _kind("contract", "contract", "shape-contract", implicit_version=1),
        _kind("contract-model", "contract model", "shape-contract-model", implicit_version=1),
        _kind("signature", "signature", "shape-signature", implicit_version=1),
        _kind("migration-receipt", "migration receipt", "shape-migration-receipt"),
        _kind("gate-schema", "gate schema", "shape-gates"),
        _kind("verify-config", "verify configuration", "shape-verify-config"),
        _kind(
            "profile-export",
            "profile export",
            "shape-profile",
            legacy_version_keys=("format_version",),
        ),
    )
}


# --- releases ------------------------------------------------------------------------------------

_RELEASE = re.compile(r"^\d{1,4}(\.\d{1,4}){1,3}$")


def parse_release(text: object) -> tuple[int, ...] | None:
    """``"1.6.0"`` as ``(1, 6, 0)``; ``None`` for anything that is not a plain release number
    (so a hostile value in a file is never echoed in a message)."""
    if not isinstance(text, str) or not _RELEASE.fullmatch(text):
        return None
    return tuple(int(p) for p in text.split("."))


def _kind_of(kind: Kind | str) -> Kind:
    return kind if isinstance(kind, Kind) else KINDS[kind]


# --- strict mode ---------------------------------------------------------------------------------

_STRICT: contextvars.ContextVar[bool | None] = contextvars.ContextVar("shape_strict", default=None)


def is_strict() -> bool:
    """Strict reading is on inside :func:`strict_formats` or when ``SHAPE_STRICT_FORMATS`` is
    ``1``, ``true`` or ``yes``."""
    local = _STRICT.get()
    if local is not None:
        return local
    return os.environ.get(STRICT_ENV, "").strip().lower() in {"1", "true", "yes"}


@contextlib.contextmanager
def strict_formats(on: bool = True) -> Iterator[None]:
    """Read in strict mode (or, with ``on=False``, in lenient mode) inside the block."""
    token = _STRICT.set(on)
    try:
        yield
    finally:
        _STRICT.reset(token)


# --- declaring and reading versions --------------------------------------------------------------


def stamp(
    kind: Kind | str,
    doc: Mapping[str, Any] | None = None,
    *,
    version: int | None = None,
    aliases: bool = True,
) -> dict[str, Any]:
    """``doc`` with its declaration: ``format``, ``version``, ``shape_version``,
    ``min_shape_version`` and (``aliases``, the 1.x default) the old version key names with the
    same value. Everything else in ``doc``, unknown fields included, is kept. Not mutating."""
    from shape import __version__

    k = _kind_of(kind)
    if not k.unified_in_body:
        raise ValueError(
            f"a {k.label} is content-addressed: its version is declared by "
            f"{k.legacy_version_keys[0]!r}, and the unified keys go in its container's manifest"
        )
    v = k.current if version is None else version
    if v not in k.first_release:
        raise ValueError(f"{k.label} has no version {v}")
    out: dict[str, Any] = {
        FORMAT_KEY: k.format,
        VERSION_KEY: v,
        WRITER_KEY: __version__,
        MINIMUM_KEY: k.first_release[v],
    }
    if aliases:
        for key in k.legacy_version_keys:
            out[key] = v
    for key, value in (doc or {}).items():
        if key not in out:
            out[key] = value
    return out


def declared_version(
    kind: Kind | str, doc: Mapping[str, Any], *, error: type[Exception] | None = None
) -> int:
    """The version ``doc`` declares, under ``version`` or an older key name.

    Every key present must be an integer of at least 1 and they must agree. A document that
    declares none has the kind's implicit version, or is a :class:`FormatError`. In strict mode an
    old key name without ``version`` is an error."""
    k = _kind_of(kind)
    keys = [VERSION_KEY, *k.legacy_version_keys]
    found: dict[str, int] = {}
    for key in keys:
        if key not in doc:
            continue
        value = doc[key]
        if not isinstance(value, int) or isinstance(value, bool):
            raise format_error_class(error)(
                f"{k.label}: {key} must be an integer, got {_safe_text(value)}"
            )
        if value < 1:
            raise format_error_class(error)(f"{k.label}: {key} must be at least 1, got {value}")
        found[key] = value
    if len(set(found.values())) > 1:
        pairs = ", ".join(f"{a}={b}" for a, b in found.items())
        raise format_error_class(error)(f"{k.label}: conflicting version keys ({pairs})")
    if not found:
        if k.implicit_version is None:
            raise format_error_class(error)(f"{k.label} declares no version")
        return k.implicit_version
    if is_strict() and VERSION_KEY not in found:
        old = next(iter(found))
        raise format_error_class(error)(
            f"{k.label}: strict mode: the version is under the old key {old!r}; the key is "
            f"{VERSION_KEY!r} (convert the file with `shape migrate`)"
        )
    return next(iter(found.values()))


def check_format(
    kind: Kind | str, doc: Mapping[str, Any], *, error: type[Exception] | None = None
) -> None:
    """``doc`` must be this kind: its ``format`` is the kind's (or an older name of it), or absent
    (older writers declared none)."""
    k = _kind_of(kind)
    if FORMAT_KEY not in doc:
        return
    value = doc[FORMAT_KEY]
    if not isinstance(value, str) or value not in (k.format, *k.legacy_formats):
        raise format_error_class(error)(
            f"not a {k.label}: its format is {_safe_text(value)}, expected {k.format!r}"
        )


def _safe_text(value: object) -> str:
    text = repr(value)
    return text if len(text) <= 40 and text.isprintable() else "not recognised"


_ERROR_CLASSES: dict[tuple[type[Exception], bool], type[FormatError]] = {}


def format_error_class(
    base: type[Exception] | None, unsupported: bool = False
) -> type[FormatError]:
    """A :class:`FormatError` (an :class:`UnsupportedVersionError` when ``unsupported``) that is
    also a ``base``, a reader's own error type, so callers that catch that type keep catching it."""
    root: type[FormatError] = UnsupportedVersionError if unsupported else FormatError
    if base is None or issubclass(root, base):
        return root
    key = (base, unsupported)
    cls = _ERROR_CLASSES.get(key)
    if cls is None:
        suffix = "Version" if unsupported else "Format"
        cls = type(f"{base.__name__}{suffix}Error", (root, base), {})
        _ERROR_CLASSES[key] = cls
    return cls


def error_class(base: type[Exception] | None) -> type[UnsupportedVersionError]:
    """:func:`format_error_class` for a version newer than this release reads."""
    cls = format_error_class(base, unsupported=True)
    assert issubclass(cls, UnsupportedVersionError)
    return cls


def check_readable(
    kind: Kind | str,
    doc: Mapping[str, Any],
    source: object = "",
    *,
    error: type[Exception] | None = None,
) -> int:
    """The version of ``doc``, if this release reads it.

    A newer version raises :class:`UnsupportedVersionError` (also ``error``, when given) naming
    the first Shape release that reads it, from the file's ``min_shape_version`` or else its
    writer's ``shape_version``. A deprecated version warns (strict mode: raises)."""
    k = _kind_of(kind)
    version = declared_version(k, doc, error=error)
    where = f"{source}: " if str(source) else ""
    if version > k.current:
        needs = _needed_release(doc)
        hint = (
            f"it needs Shape {needs} or newer"
            if needs
            else "it was written by a newer Shape release"
        )
        message = (
            f"{where}unsupported {k.label} version {version}: this Shape reads up to version "
            f"{k.current}; {hint} (upgrade Shape)"
        )
        cls = error_class(error)
        raise cls(
            message,
            kind=k.name,
            found=version,
            supported=k.current,
            min_shape_version=needs,
        )
    dep = k.deprecated.get(version)
    if dep is not None:
        text = (
            f"{where}{k.label} version {version} is deprecated since Shape {dep.since} and "
            f"removed in Shape {dep.removed_in}; convert it with `{dep.migrate_with}`"
        )
        if is_strict():
            raise format_error_class(error)(f"strict mode: {text}")
        warnings.warn(FormatDeprecationWarning(text), stacklevel=3)
    return version


def _needed_release(doc: Mapping[str, Any]) -> str | None:
    for key in (MINIMUM_KEY, WRITER_KEY):
        value = doc.get(key)
        if parse_release(value) is not None:
            assert isinstance(value, str)
            return value
    return None


def known_fields(kind: Kind | str, known: Collection[str]) -> frozenset[str]:
    """``known`` plus the declaration keys every kind accepts."""
    k = _kind_of(kind)
    return frozenset(known) | frozenset(BOOKKEEPING_KEYS) | frozenset(k.legacy_version_keys)


def check_unknown(
    kind: Kind | str,
    doc: Mapping[str, Any],
    known: Collection[str],
    *,
    error: type[Exception] | None = None,
) -> list[str]:
    """The fields of ``doc`` no field of this release takes. A reader ignores them and a rewrite
    keeps them; in strict mode they are an error."""
    k = _kind_of(kind)
    names = sorted(str(key) for key in doc if key not in known_fields(k, known))
    if names and is_strict():
        shown = ", ".join(repr(n) for n in names[:5])
        raise format_error_class(error)(f"{k.label}: strict mode: unknown field(s) {shown}")
    return names


def split_extras(
    kind: Kind | str, doc: Mapping[str, Any], known: Collection[str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(fields this release takes, the rest)``; the rest is kept and written back on rewrite."""
    allowed = known_fields(kind, known)
    take = {k: v for k, v in doc.items() if k in allowed}
    rest = {k: v for k, v in doc.items() if k not in allowed}
    return take, rest


def support_rows() -> list[dict[str, Any]]:
    """One row per kind and version for the published support window."""
    rows: list[dict[str, Any]] = []
    for k in KINDS.values():
        for v in range(1, k.current + 1):
            dep = k.deprecated.get(v)
            rows.append(
                {
                    "kind": k.name,
                    "format": k.format,
                    "version": v,
                    "first_release": k.first_release[v],
                    "status": "deprecated" if dep else "supported",
                    "removed_in": dep.removed_in if dep else "",
                }
            )
    return rows


# --- dates, decimals ------------------------------------------------------------------------------

_UTC_ISO = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{1,6})?(Z|[+-]\d\d:\d\d)$")


def utc_iso(moment: datetime | None = None) -> str:
    """``moment`` (now by default) in UTC as ISO 8601 with ``Z``: ``2026-10-03T04:05:06Z``, with
    microseconds when there are any. A naive datetime is refused: it names no instant."""
    if moment is None:
        moment = datetime.now(UTC)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("a datetime needs a timezone to be written (naive datetimes are refused)")
    utc = moment.astimezone(UTC)
    fmt = "%Y-%m-%dT%H:%M:%S.%fZ" if utc.microsecond else "%Y-%m-%dT%H:%M:%SZ"
    return utc.strftime(fmt)


def parse_utc_iso(text: str) -> datetime:
    """The aware UTC datetime of an ISO 8601 timestamp with ``Z`` or an offset. Local (naive)
    forms are refused."""
    if not isinstance(text, str) or not _UTC_ISO.fullmatch(text):
        raise ValueError(f"not a UTC ISO 8601 timestamp with a zone: {_safe_text(text)}")
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def json_default(value: Any) -> Any:
    """The ``default=`` of every JSON writer that may meet these types: a decimal is a string
    (never a binary float), a datetime is UTC ISO 8601, a date ISO, a path its POSIX form, a numpy
    scalar its Python value. Anything else is a ``TypeError``: it is not silently ``str()``-ed."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise TypeError("naive datetime: give it a timezone (UTC) before it is written")
        return utc_iso(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, PurePath):
        return value.as_posix()
    item = getattr(value, "item", None)
    if callable(item) and type(value).__module__.split(".")[0] == "numpy":
        return item()
    raise TypeError(f"{type(value).__name__} is not serializable")
