"""A cross-platform advisory file lock (standard library only).

``locked(path)`` holds an exclusive lock on a sidecar ``<path>.lock`` file while its body runs, so
a read-modify-write of ``path`` is not interleaved with another process's. The lock is on the
sidecar, not on ``path``, because ``path`` is replaced atomically (a lock on a replaced file locks
nothing). The operating system drops the lock if the holder dies, so a crash never leaves it held.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path

from shape.demo.errors import DemoError

DEFAULT_TIMEOUT = 30.0  # seconds to wait for another holder before giving up
_POLL = 0.02


def _try_lock(fd: int) -> None:
    """Take the lock without waiting; ``OSError`` when another holder has it."""
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


@contextlib.contextmanager
def locked(path: Path, timeout: float | None = None) -> Iterator[None]:
    """Hold the exclusive lock of ``path`` for the body; wait up to ``timeout`` seconds (default
    :data:`DEFAULT_TIMEOUT`) for another holder, then raise :class:`DemoError`."""
    wait = DEFAULT_TIMEOUT if timeout is None else timeout
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
    try:
        deadline = time.monotonic() + wait
        while True:
            try:
                _try_lock(fd)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise DemoError(
                        f"{path} is locked by another Shape process (waited {wait:g} s): "
                        "try again when it has finished"
                    ) from None
                time.sleep(_POLL)
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                _unlock(fd)
    finally:
        os.close(fd)
