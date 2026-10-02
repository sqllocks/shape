"""File access for the writers: a local directory, OneLake or ADLS Gen2.

Remote paths (``abfss://``, ``onelake://``) go through the core ``abfss://`` handling
(``adlfs``, the credential order of :mod:`shape.builtins.sources._azure_auth`), so a path that
profiling can read can also be written. A writer's ``credential`` is passed on as the credential
of that handling; ``filesystem`` replaces it with any fsspec filesystem (tests, other stores).

A file is never left half written: a local file is written under a temporary name and renamed
when complete, and a remote file that failed part way is removed.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO

from shape.errors import ShapeError

from ._auth import as_credential
from .onelake import is_remote, to_abfss


class Storage:
    """Writes files below one location; local when the path has no scheme."""

    def __init__(self, *, credential: Any = None, filesystem: Any = None, **options: Any) -> None:
        self._options: dict[str, Any] = dict(options)
        if credential is not None:
            self._options["credential"] = as_credential(credential)
        self._given = filesystem
        self._fs: Any = filesystem

    def _remote(self, path: str) -> tuple[Any, str]:
        uri = to_abfss(path) if path.startswith("onelake://") else path
        try:
            from shape.builtins.sources import azure
        except ImportError as exc:  # pragma: no cover - the core wheel always has it
            raise ShapeError("remote paths need sqllocks-shape[azure]") from exc
        loc = azure.parse(uri)
        if self._fs is None:
            self._fs = azure._filesystem(loc, self._options)
        return self._fs, loc.fs_path

    def is_remote(self, path: str) -> bool:
        return is_remote(path)

    def write(self, path: str, writer: Callable[[BinaryIO], None]) -> None:
        """Create ``path`` (and its folders) and let ``writer`` fill it; replaces a file."""
        if is_remote(path):
            fs, fs_path = self._remote(path)
            try:
                with fs.open(fs_path, "wb") as handle:
                    writer(handle)
            except BaseException:
                with contextlib.suppress(Exception):
                    fs.rm(fs_path)
                raise
            return
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                writer(handle)
            os.replace(tmp, target)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def write_bytes(self, path: str, data: bytes) -> None:
        def put(handle: BinaryIO) -> None:
            handle.write(data)

        self.write(path, put)

    def read_bytes(self, path: str) -> bytes:
        if is_remote(path):
            fs, fs_path = self._remote(path)
            with fs.open(fs_path, "rb") as handle:
                return bytes(handle.read())
        return Path(path).read_bytes()

    def exists(self, path: str) -> bool:
        if is_remote(path):
            fs, fs_path = self._remote(path)
            return bool(fs.exists(fs_path))
        return Path(path).exists()

    def remove(self, path: str, *, recursive: bool = False) -> None:
        """Delete a file or folder; a path that is not there is not an error."""
        if is_remote(path):
            fs, fs_path = self._remote(path)
            if fs.exists(fs_path):
                fs.rm(fs_path, recursive=recursive)
            return
        target = Path(path)
        if target.is_dir():
            import shutil

            if not recursive:
                raise ShapeError(f"{path} is a folder; remove it with recursive=True")
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
