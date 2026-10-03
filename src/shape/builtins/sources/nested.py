"""``json`` and ``xml`` sources, and the tables-mode reading shared with ``jsonl`` (W5-07).

Besides the Source protocol (``schema`` and ``read`` give ONE table: the root, or the one named
by the ``table`` option) these sources have ``read_tables(uri, **options) -> dict[str, Table]``,
``read_relationships(uri, **options)`` (the links between the tables, as
:class:`shape.generation.schema.Relationship` objects) and ``read_nested`` (both, plus warnings,
from one parse). See ``docs/SOURCES.md``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.io import ReaderError
from shape.io.nested import (
    MAX_DOCUMENT_BYTES,
    NestedTables,
    check_flatten,
    read_nested,
)
from shape.io.readers import DEFAULT_BATCH_ROWS
from shape.io.xmlread import read_xml_nested

from .files import can_open_suffix, local_path, take_options

if TYPE_CHECKING:
    from shape.generation.schema import Relationship


class NestedSource:
    """Mixin: a source whose parse gives several related tables."""

    name = ""

    def _load(self, uri: str, options: dict[str, Any]) -> tuple[NestedTables, str | None]:
        """The parsed tables and the ``table`` option (``None`` for the root)."""
        raise NotImplementedError

    def read_nested(self, uri: str, **options: Any) -> NestedTables:
        return self._load(uri, dict(options))[0]

    def read_tables(self, uri: str, **options: Any) -> dict[str, pa.Table]:
        return self.read_nested(uri, **options).tables

    def read_relationships(self, uri: str, **options: Any) -> list[Relationship]:
        return self.read_nested(uri, **options).relationships

    def _one(self, uri: str, options: dict[str, Any]) -> pa.Table:
        result, wanted = self._load(uri, options)
        if wanted is None:
            return next(iter(result.tables.values()))
        if wanted not in result.tables:
            raise ValueError(
                f"{self.name} source has no table {wanted!r}; tables: {sorted(result.tables)}"
            )
        return result.tables[wanted]

    def schema(self, uri: str, **options: Any) -> pa.Schema:
        schema: pa.Schema = self._one(uri, dict(options)).schema
        return schema

    def read(self, uri: str, **options: Any) -> Iterator[pa.RecordBatch]:
        table = self._one(uri, dict(options))
        return iter(table.to_batches(max_chunksize=DEFAULT_BATCH_ROWS))


class JsonSource(NestedSource):
    """``.json`` files: one document or an array of documents (option ``flatten``)."""

    name = "json"
    schemes = ("file",)

    def can_open(self, uri: str) -> bool:
        return can_open_suffix(uri, (".json",))

    def _load(self, uri: str, options: dict[str, Any]) -> tuple[NestedTables, str | None]:
        if not self.can_open(uri):
            raise ValueError(f"{uri!r} is not a json file")
        got = take_options(options, {"flatten", "name", "table", "max_bytes"}, self.name)
        result = read_nested(
            local_path(uri),
            lines=False,
            flatten=check_flatten(got.get("flatten")) or "struct",
            name=got.get("name"),
            max_bytes=got.get("max_bytes", MAX_DOCUMENT_BYTES),
        )
        return result, got.get("table")


class XmlSource(NestedSource):
    """``.xml`` files: the elements picked by ``record`` become rows."""

    name = "xml"
    schemes = ("file",)

    def can_open(self, uri: str) -> bool:
        return can_open_suffix(uri, (".xml",))

    def _load(self, uri: str, options: dict[str, Any]) -> tuple[NestedTables, str | None]:
        if not self.can_open(uri):
            raise ValueError(f"{uri!r} is not an xml file")
        got = take_options(options, {"record", "name", "table", "max_bytes"}, self.name)
        result = read_xml_nested(
            Path(local_path(uri)),
            record=got.get("record"),
            name=got.get("name"),
            max_bytes=got.get("max_bytes", MAX_DOCUMENT_BYTES),
        )
        return result, got.get("table")


__all__ = ["JsonSource", "NestedSource", "ReaderError", "XmlSource"]
