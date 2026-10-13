"""Where a sink's files go: a local directory or any ``fsspec`` filesystem (OneLake, ADLS Gen2).

A :class:`Store` creates files below one root. A file is written under a temporary name and
published under its final name when it is complete, so a reader (a Fabric pipeline, a storage
event trigger, a ``shape`` source) never sees a partial file:

* local: the temporary file sits next to the final one and is ``os.replace``d (atomic);
* ``fsspec``: the temporary blob sits in ``<root>/_shape_tmp/`` (readers skip names that start
  with ``_`` or ``.``) and is moved to its final name with ``fs.mv`` when complete. On a
  hierarchical namespace the move is a rename; on a flat blob namespace it is a server-side copy
  and a delete, and the final blob still appears only whole, because a block blob is not visible
  until its last block is committed.

``manifest`` writes a ``_SUCCESS`` file last, for pipelines that wait for it.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, TypeVar

T = TypeVar("T")

TEMP_DIR = "_shape_tmp"
SUCCESS_FILE = "_SUCCESS"


class PendingFile:
    """A file being written under a temporary name. ``publish`` makes it visible, ``abort``
    removes it; each is safe to call once, and ``abort`` after ``publish`` does nothing."""

    def __init__(self, store: Store, rel: str, temp: str, handle: BinaryIO, final: str) -> None:
        self._store = store
        self.rel = rel  # below the root
        self.final = final  # as the store addresses it
        self._temp = temp
        self.handle = handle
        self._done = False

    def publish(self) -> None:
        if self._done:
            return
        self._done = True
        try:
            self._store._publish(self)
        except BaseException:
            self._discard()
            raise

    def abort(self) -> None:
        if self._done:
            return
        self._done = True
        self._discard()

    def _discard(self) -> None:
        with contextlib.suppress(Exception):
            self.handle.close()
        with contextlib.suppress(Exception):
            self._store._remove(self._temp)


class Store:
    """Files below one root; subclasses supply the filesystem calls."""

    def create(self, rel: str) -> PendingFile:
        """Start the file ``rel`` (a path below the root, ``/`` separated)."""
        raise NotImplementedError

    def exists(self, rel: str) -> bool:
        raise NotImplementedError

    def names(self, rel_dir: str) -> list[str]:
        """The names of the files directly in ``rel_dir`` (empty when it does not exist)."""
        raise NotImplementedError

    def location(self, rel: str) -> str:
        """``rel`` as the store spells it, for messages."""
        raise NotImplementedError

    def delete(self, rel: str) -> None:
        """Remove the file ``rel`` (nothing happens when it is gone)."""
        raise NotImplementedError

    def _publish(self, pending: PendingFile) -> None:
        raise NotImplementedError

    def _remove(self, temp: str) -> None:
        raise NotImplementedError

    def put_bytes(self, rel: str, data: bytes) -> None:
        pending = self.create(rel)
        try:
            pending.handle.write(data)
            pending.publish()
        except BaseException:
            pending.abort()
            raise

    def mark_success(self, rel_dir: str = "") -> None:
        """Write the ``_SUCCESS`` file in ``rel_dir``."""
        self.put_bytes(str(PurePosixPath(rel_dir) / SUCCESS_FILE), b"")


@contextlib.contextmanager
def replace_atomically(target: Path) -> Iterator[Path]:
    """The path to write instead of ``target``: a hidden temporary file next to it, renamed onto
    ``target`` when the block completes and removed when it raises, so a failed or interrupted
    write keeps the previous file and a reader never sees a partial one.

    A symlink is written through; the mode of an existing file is kept; a device or a pipe
    (nothing to replace) is written in place."""
    if target.exists() and not target.is_file():
        yield target
        return
    final = Path(os.path.realpath(target)) if target.is_symlink() else target
    temp = final.with_name(f".shape-{uuid.uuid4().hex[:12]}.tmp")
    try:
        yield temp
        if final.exists():
            shutil.copymode(final, temp)
        os.replace(temp, final)
    except BaseException:
        with contextlib.suppress(OSError):
            temp.unlink()
        raise


def _clean(rel: str) -> str:
    path = PurePosixPath(rel.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{rel!r} leaves the output location")
    if not path.parts:
        raise ValueError(f"{rel!r} names no file below the output location")
    return str(path)


_TEMP_NAME_KEEP = 200  # bytes of the final name kept in a temporary name (NAME_MAX is 255)


def _temp_stem(name: str) -> str:
    """The start of ``name`` that a temporary name repeats, short enough that the temporary
    name stays a valid file name whenever ``name`` is one."""
    return name.encode("utf-8")[:_TEMP_NAME_KEEP].decode("utf-8", errors="ignore")


class LocalStore(Store):
    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)

    def _path(self, rel: str) -> Path:
        return self.root / _clean(rel)

    def create(self, rel: str) -> PendingFile:
        target = self._path(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.parent / f".{_temp_stem(target.name)}.{uuid.uuid4().hex[:8]}.tmp"
        return PendingFile(self, rel, str(temp), open(temp, "wb"), str(target))  # noqa: SIM115

    def exists(self, rel: str) -> bool:
        return self._path(rel).exists()

    def names(self, rel_dir: str) -> list[str]:
        folder = self._path(rel_dir) if rel_dir else self.root
        if not folder.is_dir():
            return []
        return sorted(p.name for p in folder.iterdir() if p.is_file())

    def location(self, rel: str) -> str:
        return str(self._path(rel))

    def delete(self, rel: str) -> None:
        with contextlib.suppress(FileNotFoundError):
            self._path(rel).unlink()

    def _publish(self, pending: PendingFile) -> None:
        pending.handle.close()
        os.replace(pending._temp, pending.final)

    def _remove(self, temp: str) -> None:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp)


class FsspecStore(Store):
    """A filesystem object (``adlfs``, ``memory``, local, ...) and a root path inside it.

    A file is spooled (in memory up to ``spool_bytes``, then in a temporary local file) while it is
    written, and uploaded when it is complete: an upload that fails with a transient error is
    repeated from the spool, up to ``retries`` times with a doubling pause (``sleep`` is
    injectable for tests), and the remote object only ever comes into being whole.
    ``translate`` maps a failure to a clearer exception (or returns ``None`` to keep it)."""

    def __init__(
        self,
        fs: Any,
        root: str,
        *,
        label: str | None = None,
        retries: int = 3,
        spool_bytes: int = 32 << 20,
        sleep: Callable[[float], None] = time.sleep,
        translate: Callable[[BaseException], BaseException | None] | None = None,
    ) -> None:
        self.fs = fs
        self.root = root.rstrip("/")
        self._label = label or self.root
        self._retries = max(0, retries)
        self._spool_bytes = spool_bytes
        self._sleep = sleep
        self._translate = translate

    def _path(self, rel: str) -> str:
        return f"{self.root}/{_clean(rel)}"

    def _call(self, call: Callable[[], T], what: str) -> T:
        """``call`` with retries for transient failures; other failures are translated."""
        pause = 0.5
        for attempt in range(self._retries + 1):
            try:
                return call()
            except BaseException as exc:
                mapped = self._translate(exc) if self._translate is not None else None
                if mapped is not None:
                    raise mapped from exc
                if attempt >= self._retries or not is_transient(exc):
                    raise
                self._sleep(pause)
                pause = min(pause * 2, 30.0)
        raise AssertionError(what)  # pragma: no cover

    def create(self, rel: str) -> PendingFile:
        final = self._path(rel)
        name = PurePosixPath(final).name
        temp = f"{self.root}/{TEMP_DIR}/{uuid.uuid4().hex[:12]}-{_temp_stem(name)}"
        spool = tempfile.SpooledTemporaryFile(max_size=self._spool_bytes)  # noqa: SIM115
        return PendingFile(self, rel, temp, spool, final)  # type: ignore[arg-type]

    def _publish(self, pending: PendingFile) -> None:
        spool = pending.handle

        def upload() -> None:
            spool.seek(0)
            with self.fs.open(pending._temp, "wb") as out:
                shutil.copyfileobj(spool, out, 8 << 20)

        self._call(upload, "upload")
        spool.close()
        self._call(lambda: self.fs.mv(pending._temp, pending.final), "publish")

    def exists(self, rel: str) -> bool:
        return bool(self._call(lambda: self.fs.exists(self._path(rel)), "exists"))

    def names(self, rel_dir: str) -> list[str]:
        folder = self._path(rel_dir) if rel_dir else self.root

        def ls() -> list[str]:
            try:
                entries = self.fs.ls(folder, detail=True)
            except FileNotFoundError:
                return []
            return sorted(
                PurePosixPath(str(e["name"])).name for e in entries if e.get("type") == "file"
            )

        return self._call(ls, "list")

    def location(self, rel: str) -> str:
        return f"{self._label}/{_clean(rel)}"

    def delete(self, rel: str) -> None:
        def rm() -> None:
            with contextlib.suppress(FileNotFoundError):
                self.fs.rm(self._path(rel))

        self._call(rm, "delete")

    def _remove(self, temp: str) -> None:
        with contextlib.suppress(Exception):
            self.fs.rm(temp)

    def probe(self) -> None:
        """Fail now, with a clear message, when the root cannot be reached (wrong container, no
        permission) instead of when the first file is uploaded."""
        self._call(lambda: self.fs.exists(self.root), "probe")


def is_transient(exc: BaseException) -> bool:
    """A failure worth repeating: a dropped connection, a timeout, throttling, a server error."""
    if isinstance(exc, (PermissionError, FileNotFoundError, FileExistsError, ValueError)):
        return False
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    status = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "response", None), "status_code", None
    )
    if isinstance(status, int):
        return status in (408, 429) or status >= 500
    name = type(exc).__name__
    return name in {"ServiceRequestError", "ServiceResponseError", "IncompleteRead", "ClientError"}
