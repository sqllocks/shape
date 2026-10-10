"""Keep public exports, docstrings and generated reference coverage in step."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import shape

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "docs_public_api", ROOT / "scripts/docs_public_api.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_public_docstrings() -> None:
    assert module.missing_docstrings() == []
    for name in shape.__all__:
        assert inspect.getdoc(getattr(shape, name)), name


def test_public_reference_coverage() -> None:
    reference = module.reference_markdown()
    for mod, names in module.inventory().items():
        assert f"## {mod}\n" in reference
        for name in names:
            assert f"`{name}`" in reference or f"### shape.{name} {{ #shape.{name} }}" in reference
    assert "reference/api.md" in (ROOT / "mkdocs.yml").read_text()
