"""Shared helpers for the W5-06 importer tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.generation.engine import Engine
from shape.generation.schema import GenSchema
from shape.generation.spec_edit import SpecDocument

FIXTURES = Path(__file__).parent / "fixtures"


def generate(doc: SpecDocument, rows: int = 20) -> dict[str, pa.Table]:
    """Generate every table of an imported spec at a tiny scale; the spec must validate."""
    assert [p for p in doc.validate() if p.level == "error"] == []
    data = doc.to_dict()
    for table in data["generation"]["scales"]["small"]:
        data["generation"]["scales"]["small"][table] = rows
    tables: dict[str, pa.Table] = Engine(GenSchema.from_dict(data), seed=5).generate().tables
    for name, t in tables.items():
        assert t.num_rows > 0, name
    return tables


def columns(doc: SpecDocument, table: str) -> dict[str, dict[str, Any]]:
    cols: dict[str, dict[str, Any]] = doc.to_dict()["tables"][table]["columns"]
    return cols
