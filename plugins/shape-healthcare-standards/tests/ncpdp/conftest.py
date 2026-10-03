from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from shape_healthcare_standards import testing
from shape_healthcare_standards.common import TableSet
from shape_healthcare_standards.ncpdp.layout import Layout

LAYOUT_PATH = Path(__file__).parent / "synthetic_layout.json"


@pytest.fixture
def layout_doc() -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
    return doc


@pytest.fixture
def layout() -> Layout:
    return Layout.load(LAYOUT_PATH)


@pytest.fixture
def tables() -> TableSet:
    return TableSet(testing.sample_tables())
