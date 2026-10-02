"""One error policy for the ``shape`` command line.

An expected error (bad input, a missing file, a ref the registry does not hold) becomes one line,
``shape: error: MESSAGE``, and exit code 2. A bug is not an expected error: it keeps its
traceback. ``--debug`` (before the command) or ``SHAPE_DEBUG=1`` keeps the traceback for the
expected ones too.

Exit codes (see ``docs/CLI.md``): 0 ok, 1 a check failed or drift was found, 2 bad input or an
unreadable file, 3 and above are a command's own verdict (a certificate below its threshold, a
failed contract, an incompatible change).
"""

from __future__ import annotations

import os
import re
import sys
import zipfile
from collections.abc import Callable

from shape.errors import ShapeError

EXIT_INPUT_ERROR = 2

#: The exception types that mean "the user gave something Shape cannot use". ``JSONDecodeError``
#: is a ``ValueError`` and ``FileNotFoundError`` an ``OSError``, so both are covered.
EXPECTED: tuple[type[BaseException], ...] = (
    ShapeError,
    OSError,
    ValueError,
    KeyError,
    ImportError,
    NotImplementedError,
    RecursionError,
    zipfile.BadZipFile,
)

_NOT_FOUND = re.compile(r"^(?:source|file)\s+not found:\s*(?P<path>.+)$", re.IGNORECASE)


_debug = False


def set_debug(on: bool) -> None:
    """Set by the ``--debug`` global option for the run in progress."""
    global _debug
    _debug = on


def debug_enabled(flag: bool = False) -> bool:
    return flag or _debug or os.environ.get("SHAPE_DEBUG", "") not in ("", "0")


def describe(exc: BaseException) -> str:
    """The one-line message of an expected error."""
    if isinstance(exc, FileNotFoundError):
        if exc.filename is not None:
            return f"file not found: {exc.filename}"
        found = _NOT_FOUND.match(str(exc))
        return f"file not found: {found['path']}" if found else str(exc)
    if isinstance(exc, KeyError):
        key = exc.args[0] if exc.args else ""
        return f"missing key {key!r} in the input"
    if isinstance(exc, ValueError) and type(exc).__name__ == "JSONDecodeError":
        return f"not valid JSON: {exc}"
    if isinstance(exc, OSError) and exc.strerror:
        where = f" ({exc.filename})" if exc.filename is not None else ""
        return f"{exc.strerror}{where}"
    return " ".join(str(exc).split()) or type(exc).__name__


def fail(exc: BaseException) -> int:
    """Print ``exc`` as an expected error; returns the exit code."""
    print(f"shape: error: {describe(exc)}", file=sys.stderr)
    return EXIT_INPUT_ERROR


def guarded(fn: Callable[[], int], *, debug: bool = False) -> int:
    """Run ``fn`` and turn an expected error into a message and exit code 2."""
    try:
        return fn()
    except EXPECTED as exc:
        if debug_enabled(debug):
            raise
        return fail(exc)
