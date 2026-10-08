"""Reading a JSON or YAML source document with the places of its elements (W5-06).

JSON is always read; YAML needs PyYAML (the ``yaml`` extra) and is read through the bounded safe
loader. A malformed document raises :class:`~shape.importers.core.ImportFormatError` with the
file and, for JSON, the line.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shape.generation.spec_edit import _Scan
from shape.importers.core import ImportFormatError


@dataclass
class Document:
    """A parsed document. ``line(pointer)`` is the line of a JSON Pointer (``None`` where the
    format gives none, as for YAML)."""

    path: str
    data: Any
    positions: dict[str, tuple[int, int]] = field(default_factory=dict)

    def line(self, pointer: str) -> int | None:
        while pointer and pointer not in self.positions:
            pointer = pointer.rsplit("/", 1)[0]
        found = self.positions.get(pointer)
        return found[0] if found else None


def read_text(path: str | Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ImportFormatError("not a UTF-8 text file", file=str(path)) from exc


def load_document(path: str | Path) -> Document:
    """The JSON or YAML document at ``path`` (by extension: ``.yaml`` and ``.yml`` are YAML,
    anything else JSON)."""
    name = str(path)
    text = read_text(path)
    if Path(name).suffix.lower() in (".yaml", ".yml"):
        return _load_yaml(name, text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ImportFormatError(
            f"not valid JSON ({exc.msg}, column {exc.colno})", file=name, line=exc.lineno
        ) from exc
    except RecursionError as exc:
        raise ImportFormatError("nested too deeply", file=name) from exc
    return Document(name, data, _Scan(text).positions)


def _load_yaml(name: str, text: str) -> Document:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ImportFormatError(
            "reading YAML needs PyYAML (pip install 'sqllocks-shape[yaml]'), or give a JSON file",
            file=name,
        ) from exc
    from shape.security.yamlsafe import safe_load_yaml

    try:
        data = safe_load_yaml(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        raise ImportFormatError(
            f"not valid YAML ({getattr(exc, 'problem', None) or exc})",
            file=name,
            line=mark.line + 1 if mark is not None else None,
        ) from exc
    except ValueError as exc:
        raise ImportFormatError(str(exc), file=name) from exc
    return Document(name, data)
