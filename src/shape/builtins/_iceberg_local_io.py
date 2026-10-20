"""Native local-file decoding for the optional Iceberg catalog's Arrow I/O.

Imported lazily by PyIceberg's FileIO loader, after the Iceberg extra is selected.
"""

from collections.abc import Callable
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import urlparse

from fsspec.implementations.dirfs import DirFileSystem  # type: ignore[import-untyped]
from fsspec.implementations.local import LocalFileSystem  # type: ignore[import-untyped]
from pyarrow.fs import FSSpecHandler, PyFileSystem  # type: ignore[import-untyped]
from pyiceberg.io.pyarrow import PyArrowFileIO

from shape.builtins._iceberg import _file_path


class _LocalPaths(DirFileSystem):  # type: ignore[misc]
    def __init__(self, native: Callable[[str], str]) -> None:
        super().__init__(path=".", fs=LocalFileSystem(auto_mkdir=True))
        self.path = ""
        self._native = native

    def _join(self, path: Any) -> Any:
        if isinstance(path, str):
            return self._native(path)
        if isinstance(path, dict):
            return {self._join(key): value for key, value in path.items()}
        return [self._join(item) for item in path]


class LocalFileIO(PyArrowFileIO):
    """Normalize local URI paths for both metadata I/O and Arrow scan filesystems."""

    def _native_path(self, path: str) -> str:
        parsed = urlparse(path)
        if parsed.scheme == "file" and parsed.netloc in ("", "localhost"):
            path = parsed.path
        warehouse = urlparse(str(self.properties.get("warehouse", "")))
        if warehouse.scheme == "file" and warehouse.netloc in ("", "localhost"):
            encoded = warehouse.path.rstrip("/") or "/"
            native = _file_path(encoded)
            for prefix in (native, native.replace("\\", "/")):
                if (
                    path == prefix
                    or path.startswith(prefix + "/")
                    or path.startswith(prefix + "\\")
                ):
                    return path
            for prefix in (encoded, "localhost" + encoded):
                if path == prefix or path.startswith(prefix.rstrip("/") + "/"):
                    suffix = path[len(prefix) :].lstrip("/")
                    factory = PureWindowsPath if PureWindowsPath(native).drive else PurePosixPath
                    return str(factory(native).joinpath(*suffix.split("/")))
        return _file_path(path)

    def _initialize_local_fs(self) -> Any:
        # PyIceberg0.9's Arrow scan passes raw URI paths directly to the filesystem.
        return PyFileSystem(FSSpecHandler(_LocalPaths(self._native_path)))
