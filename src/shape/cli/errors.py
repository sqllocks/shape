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

import contextlib
import os
import re
import sys
import zipfile
from collections.abc import Callable, Iterator

from shape.errors import ShapeError

EXIT_INPUT_ERROR = 2
#: The reader of standard output went away (``shape ... | head``): 128 + SIGPIPE, as a shell
#: reports a Unix tool that stopped on a closed pipe.
EXIT_PIPE_CLOSED = 141

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
        if isinstance(key, str) and " " in key.strip():
            return " ".join(key.split())  # a message, not the name of a missing key
        return f"missing key {key!r} in the input"
    if isinstance(exc, ValueError) and type(exc).__name__ == "JSONDecodeError":
        return f"not valid JSON: {exc}"
    if isinstance(exc, OSError) and exc.strerror:
        where = f" ({exc.filename})" if exc.filename is not None else ""
        return f"{exc.strerror}{where}"
    return " ".join(str(exc).split()) or type(exc).__name__


def fail(exc: BaseException) -> int:
    """Print ``exc`` (secrets redacted) as an expected error; returns the exit code: 2, or the
    ``exit_code`` the error declares (a verdict on the data, such as constraints that do not hold
    after a load, is 1)."""
    from shape.security.redact import redact_text

    # A message can quote a connection string or a URI with a key in it: never print the secret.
    print(f"shape: error: {redact_text(describe(exc))}", file=sys.stderr)
    code = getattr(exc, "exit_code", EXIT_INPUT_ERROR)
    return code if isinstance(code, int) and not isinstance(code, bool) else EXIT_INPUT_ERROR


@contextlib.contextmanager
def quiet_notices() -> Iterator[None]:
    """Reads raise no "not verified" notice in this block: for files Shape itself wrote a moment
    ago (temporary copies, a self-test), whose names mean nothing to the user."""
    from shape.artifact.io import set_notice_handler

    previous = set_notice_handler(lambda _message: None)
    try:
        yield
    finally:
        set_notice_handler(previous)


def pipe_closed() -> int:
    """Stop quietly when standard output's reader has gone: point standard output at the null
    device so the interpreter's last flush cannot fail again, and return 141."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except (OSError, ValueError):  # no file descriptor (a test's captured stdout)
        pass
    return EXIT_PIPE_CLOSED


def guarded(fn: Callable[[], int], *, debug: bool = False) -> int:
    """Run ``fn`` and turn an expected error into a message and exit code 2 (a closed standard
    output is not an error: exit 141, nothing printed)."""
    try:
        return fn()
    except BrokenPipeError:
        return pipe_closed()
    except EXPECTED as exc:
        if debug_enabled(debug):
            raise
        return fail(exc)
