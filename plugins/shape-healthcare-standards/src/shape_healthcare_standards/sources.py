"""``shape.sources`` for X12 and HL7 v2 files (structure only).

See ``docs/plugins/healthcare-standards.md``.

Each source has the Source protocol (``schema`` and ``read`` give ONE table: ``x12_segment`` or
``hl7_segment``, or the one named by the ``table`` option) and ``read_tables`` /
``read_relationships`` / ``read_nested`` for all of them.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlparse

import pyarrow as pa  # type: ignore[import-untyped]

from .hl7v2.read import parse_hl7
from .hl7v2.read import segment_table as hl7_segment_table
from .hl7v2.read import segment_tables as hl7_segment_tables
from .x12.read import loop_tables, parse_x12, problem_table
from .x12.read import segment_table as x12_segment_table

if TYPE_CHECKING:
    from shape.generation.schema import Relationship

SHAPE_API = "1.0"
MAX_BYTES = 256 * 1024 * 1024
BATCH_ROWS = 65_536


def _path(uri: str, scheme: str) -> Path | None:
    """The file ``uri`` names (a path, ``file://`` or ``<scheme>://``), or ``None``."""
    parsed = urlparse(uri)
    if parsed.scheme == scheme:
        return Path(unquote(uri[len(scheme) + 3 :]))
    if parsed.scheme == "file":
        return Path(unquote(parsed.path))
    if parsed.scheme == "" or len(parsed.scheme) == 1:  # a plain path (or a Windows drive)
        return Path(uri)
    return None


def _text(path: Path, max_bytes: int) -> str:
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(f"{path} is {size} bytes, over the {max_bytes}-byte limit (max_bytes)")
    data = path.read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _options(options: dict[str, Any], allowed: set[str], source: str) -> dict[str, Any]:
    unknown = sorted(set(options) - allowed)
    if unknown:
        raise TypeError(f"{source} source got unexpected option(s) {unknown}")
    return dict(options)


class _Standard:
    name = ""
    schemes: tuple[str, ...] = ()
    suffixes: tuple[str, ...] = ()
    root = ""
    allowed: set[str] = set()

    def can_open(self, uri: str) -> bool:
        parsed = urlparse(uri)
        if parsed.scheme == self.name:
            return True
        path = _path(uri, self.name)
        return path is not None and path.suffix.lower() in self.suffixes

    def _file(self, uri: str) -> Path:
        path = _path(uri, self.name)
        if path is None or not self.can_open(uri):
            raise ValueError(f"{uri!r} is not a {self.name} file")
        return path

    def _tables(
        self, uri: str, options: dict[str, Any]
    ) -> tuple[dict[str, pa.Table], list[Relationship]]:
        raise NotImplementedError

    def read_nested(
        self, uri: str, **options: Any
    ) -> tuple[dict[str, pa.Table], list[Relationship]]:
        """``(tables, relationships)``."""
        return self._tables(uri, _options(options, self.allowed, self.name))

    def read_tables(self, uri: str, **options: Any) -> dict[str, pa.Table]:
        return self.read_nested(uri, **options)[0]

    def read_relationships(self, uri: str, **options: Any) -> list[Relationship]:
        return self.read_nested(uri, **options)[1]

    def _one(self, uri: str, options: dict[str, Any]) -> pa.Table:
        wanted = options.pop("table", None) or self.root
        tables = self._tables(uri, _options(options, self.allowed, self.name))[0]
        if wanted not in tables:
            raise ValueError(
                f"{self.name} source has no table {wanted!r}; tables: {sorted(tables)}"
            )
        return tables[wanted]

    def schema(self, uri: str, **options: Any) -> pa.Schema:
        schema: pa.Schema = self._one(uri, dict(options)).schema
        return schema

    def read(self, uri: str, **options: Any) -> Iterator[pa.RecordBatch]:
        return iter(self._one(uri, dict(options)).to_batches(max_chunksize=BATCH_ROWS))


class X12Source(_Standard):
    """X12 interchanges: ``x12_segment`` and ``x12_problems``, and with ``loops="tables"`` one
    table per loop of an 835 (005010X221A1)."""

    name = "x12"
    schemes = ("x12", "file")
    suffixes = (".x12", ".edi")
    root = "x12_segment"
    allowed = {"loops", "strict", "table", "max_bytes"}

    def _tables(
        self, uri: str, options: dict[str, Any]
    ) -> tuple[dict[str, pa.Table], list[Relationship]]:
        options.pop("table", None)
        loops = options.get("loops")
        if loops not in (None, "tables"):
            raise ValueError(f"loops must be None or 'tables', not {loops!r}")
        path = self._file(uri)
        result = parse_x12(
            _text(path, options.get("max_bytes", MAX_BYTES)), strict=options.get("strict", True)
        )
        tables = {
            "x12_segment": x12_segment_table(result.segments),
            "x12_problems": problem_table(result.problems),
        }
        rels: list[Relationship] = []
        if loops == "tables":
            more, rels = loop_tables(result)
            tables.update(more)
        return tables, rels


class Hl7v2Source(_Standard):
    """HL7 v2 messages: ``hl7_segment``, and with ``segments="tables"`` ``hl7_message`` and one
    table per segment id."""

    name = "hl7v2"
    schemes = ("hl7v2", "file")
    suffixes = (".hl7",)
    root = "hl7_segment"
    allowed = {"segments", "table", "max_bytes"}

    def _tables(
        self, uri: str, options: dict[str, Any]
    ) -> tuple[dict[str, pa.Table], list[Relationship]]:
        options.pop("table", None)
        segments = options.get("segments")
        if segments not in (None, "tables"):
            raise ValueError(f"segments must be None or 'tables', not {segments!r}")
        path = self._file(uri)
        messages = parse_hl7(_text(path, options.get("max_bytes", MAX_BYTES)))
        tables = {"hl7_segment": hl7_segment_table(messages)}
        rels: list[Relationship] = []
        if segments == "tables":
            more, rels = hl7_segment_tables(messages)
            tables.update(more)
        return tables, rels
