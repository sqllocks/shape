"""File sources over :mod:`shape.io.readers`: a local path or ``file://`` URI to batches."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import pyarrow as pa  # type: ignore[import-untyped]

from shape.io import CsvOptions, file_kind, open_source


def local_path(uri: str) -> Path:
    """The local path of ``uri``: a plain path or a ``file://`` URI."""
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        path = unquote(parsed.path)
        if len(path) > 2 and path[0] == "/" and path[1].isalpha() and path[2] == ":":
            path = path[1:]  # file:///C:/x names the drive path C:/x (what Path.as_uri() writes)
        return Path(path)
    return Path(uri)


def _csv_options(options: dict[str, Any]) -> CsvOptions | None:
    given = options.pop("csv", None)
    if given is None:
        return None
    return given if isinstance(given, CsvOptions) else CsvOptions(**given)


class _FileSource:
    name = ""
    schemes = ("file",)

    def can_open(self, uri: str) -> bool:
        parsed = urlparse(uri)
        if parsed.scheme not in ("", "file") and len(parsed.scheme) > 1:
            return False
        return file_kind(local_path(uri)) == self.name

    def _open(self, uri: str, **options: Any) -> Any:
        if not self.can_open(uri):
            raise ValueError(f"{uri!r} is not a {self.name} file")
        return open_source(str(local_path(uri)), csv=_csv_options(options), **options)

    def schema(self, uri: str, **options: Any) -> pa.Schema:
        schema: pa.Schema = self._open(uri, **options).schema
        return schema

    def read(self, uri: str, **options: Any) -> Iterator[pa.RecordBatch]:
        return iter(self._open(uri, **options).batches())


class CsvSource(_FileSource):
    name = "csv"


class ParquetSource(_FileSource):
    name = "parquet"


class JsonlSource(_FileSource):
    name = "jsonl"


class IpcSource(_FileSource):
    name = "ipc"
