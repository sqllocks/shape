"""The layout marker of a registry directory (``docs/specs/STATE_AND_COMPATIBILITY.md``).

A registry is a directory tree, so its version lives in one small file at its root, declared like
every other persisted file (``format``, ``version``, ``shape_version``, ``min_shape_version``). A
directory without the file was written before it existed and has layout version 1; opening it
writes the marker. A layout newer than this release reads is refused with the minimum release.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path

from shape import compat

MAX_BYTES = 64 * 1024


def open_layout(root: Path, filename: str, kind: str, error: type[Exception]) -> int:
    """The layout version of the registry at ``root``; writes the marker when it is missing.

    ``error`` is the registry's own error type, raised for a marker that is unreadable, of
    another kind or newer than this release reads."""
    path = root / filename
    if path.is_file():
        try:
            raw = path.read_bytes()[: MAX_BYTES + 1]
            doc = json.loads(raw) if len(raw) <= MAX_BYTES else None
        except (OSError, ValueError) as e:
            raise error(f"{path} is not a registry layout marker ({e})") from e
        if not isinstance(doc, dict):
            raise error(f"{path} is not a registry layout marker")
        compat.check_format(kind, doc, error=error)
        return compat.check_readable(kind, doc, path, error=error)
    marker = compat.stamp(kind, {}, aliases=False)
    with contextlib.suppress(OSError):  # a read-only registry is still readable
        fd, tmp = tempfile.mkstemp(dir=root, prefix=".tmp-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(marker, indent=2, sort_keys=True) + "\n")
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
    return compat.KINDS[kind].current
