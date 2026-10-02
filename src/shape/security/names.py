"""File-name safety for names that come from data (table names in a schema, profile or artifact).

A table name becomes a file or directory name in every writer. ``Path / "/abs"`` discards the
left side and ``..`` climbs out, so a hostile schema or profile could write outside the output
directory. :func:`safe_name` rejects such a name; :func:`contained` is the second line of defence
at the point of use.
"""

from __future__ import annotations

from pathlib import Path


def is_safe_name(name: object) -> bool:
    """True when ``name`` is one plain path component: no separator, drive, NUL or dot-dot."""
    return (
        isinstance(name, str)
        and bool(name)
        and name not in (".", "..")
        and not any(c in name for c in ("/", "\\", "\x00"))
        and ":" not in name[:2]
    )


def safe_name(name: object, what: str = "table name") -> str:
    """``name`` unchanged, or a ``ValueError`` when it cannot be a single file-name component."""
    if not is_safe_name(name):
        raise ValueError(f"unsafe {what} {name!r}: it must be a plain name, not a path")
    assert isinstance(name, str)
    return name


def contained(root: str | Path, name: str, suffix: str = "") -> Path:
    """``root / (name + suffix)``, checked to stay inside ``root``."""
    safe_name(name)
    base = Path(root)
    target = base / f"{name}{suffix}"
    if not target.resolve().is_relative_to(base.resolve()):
        raise ValueError(f"unsafe name {name!r}: the path leaves {base}")
    return target
