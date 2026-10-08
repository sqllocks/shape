"""File sources over :mod:`shape.io.readers`: a local path or ``file://`` URI to batches."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import pyarrow as pa  # type: ignore[import-untyped]

from shape.io import CsvOptions, file_kind, open_source
from shape.io.nested import MAX_DOCUMENT_BYTES, NestedTables, check_flatten, read_nested
from shape.io.readers import DEFAULT_BATCH_ROWS
from shape.plugins import schemes

if TYPE_CHECKING:
    from shape.generation.schema import Relationship


def local_path(uri: str) -> Path:
    """The local path of ``uri``: a plain path or a ``file://`` URI."""
    if urlparse(uri).scheme == "file":
        path = schemes.file_uri_path(uri)  # a UNC host and, on Windows, the drive (#241)
        if len(path) > 2 and path[0] == "/" and path[1].isalpha() and path[2] == ":":
            path = path[1:]  # file:///C:/x names the drive path C:/x (what Path.as_uri() writes)
        return Path(path)
    return Path(uri)


def take_options(options: dict[str, Any], allowed: set[str], source: str) -> dict[str, Any]:
    """The entries of ``options`` named in ``allowed`` (removed from ``options``); anything else
    left over is a ``TypeError`` naming the source."""
    taken = {k: options.pop(k) for k in list(options) if k in allowed}
    if options:
        raise TypeError(f"{source} source got unexpected option(s) {sorted(options)}")
    return taken


def can_open_suffix(uri: str, suffixes: tuple[str, ...]) -> bool:
    parsed = urlparse(uri)
    if parsed.scheme not in ("", "file") and len(parsed.scheme) > 1:
        return False
    return local_path(uri).suffix.lower() in suffixes


def output_path(uri: str) -> Path:
    """The local path a sink writes to: :func:`local_path`, but an empty location is an error
    (``Path("")`` is the current directory, which an unset setting never meant)."""
    if not str(uri).strip():
        raise ValueError("the output location is empty (name the current directory with '.')")
    return local_path(uri)


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
    """JSON lines. ``flatten="struct"`` (the default) keeps nested objects as structs and arrays
    as lists; ``flatten="tables"`` splits them into related tables (``docs/SOURCES.md``)."""

    name = "jsonl"

    def _tables_mode(self, options: dict[str, Any]) -> dict[str, Any] | None:
        """The nested options when ``flatten="tables"``, else ``None`` (and ``options`` loses
        ``flatten``)."""
        flatten = check_flatten(options.pop("flatten", None))
        if flatten != "tables":
            if "table" in options:
                raise ValueError("the table option needs flatten='tables'")
            return None
        return take_options(options, {"name", "table", "max_bytes"}, self.name)

    def _nested(self, uri: str, got: dict[str, Any]) -> tuple[NestedTables, str | None]:
        if not self.can_open(uri):
            raise ValueError(f"{uri!r} is not a jsonl file")
        result = read_nested(
            local_path(uri),
            lines=True,
            flatten="tables",
            name=got.get("name"),
            max_bytes=got.get("max_bytes", MAX_DOCUMENT_BYTES),
        )
        return result, got.get("table")

    def read_nested(self, uri: str, **options: Any) -> NestedTables:
        """The tables of a flattened file (``flatten="tables"`` is implied)."""
        opts = dict(options, flatten="tables")
        got = self._tables_mode(opts)
        assert got is not None
        return self._nested(uri, got)[0]

    def read_tables(self, uri: str, **options: Any) -> dict[str, pa.Table]:
        return self.read_nested(uri, **options).tables

    def read_relationships(self, uri: str, **options: Any) -> list[Relationship]:
        return self.read_nested(uri, **options).relationships

    def _one(self, uri: str, got: dict[str, Any]) -> pa.Table:
        result, wanted = self._nested(uri, got)
        if wanted is None:
            return next(iter(result.tables.values()))
        if wanted not in result.tables:
            raise ValueError(
                f"jsonl source has no table {wanted!r}; tables: {sorted(result.tables)}"
            )
        return result.tables[wanted]

    def schema(self, uri: str, **options: Any) -> pa.Schema:
        got = self._tables_mode(options)
        if got is None:
            return super().schema(uri, **options)
        schema: pa.Schema = self._one(uri, got).schema
        return schema

    def read(self, uri: str, **options: Any) -> Iterator[pa.RecordBatch]:
        got = self._tables_mode(options)
        if got is None:
            return super().read(uri, **options)
        return iter(self._one(uri, got).to_batches(max_chunksize=DEFAULT_BATCH_ROWS))


class IpcSource(_FileSource):
    name = "ipc"
