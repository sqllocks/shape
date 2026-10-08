"""W7-07: the written promise for the stable Python modules (``docs/API_STABILITY.md``).

Each claim the page makes is tied to a test: ``__all__`` is explicit, the page lists exactly the
exported names, and every documented behaviour names a test that exists (and is below).
"""

from __future__ import annotations

import ast
import importlib
import json
import re
from pathlib import Path
from types import ModuleType

import pytest

from shape.generation import spec_edit, spec_schema
from shape.generation.spec_edit import SpecDocument, SpecProblem, validate_text
from shape.generation.spec_schema import published_schema

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "docs" / "API_STABILITY.md"
STABLE = {
    "shape.generation.spec_edit": [
        "Position",
        "SpecDocument",
        "SpecError",
        "SpecProblem",
        "validate_text",
    ],
    "shape.generation.spec_schema": [
        "build_schema",
        "published_schema",
        "render",
        "strategy_names",
    ],
}


def _section(text: str, heading: str, level: str = "##") -> str:
    m = re.search(
        rf"^{level} {re.escape(heading)}\n(.*?)(?=^#{{1,{len(level)}}} |\Z)", text, re.S | re.M
    )
    assert m, f"API_STABILITY.md has no '{level} {heading}' section"
    return m.group(1)


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


# ---- item 1: explicit public surface ----


def check_all(module: ModuleType) -> list[str]:
    """Problems with a module's ``__all__`` (empty if it is a sound public surface)."""
    names = getattr(module, "__all__", None)
    if names is None:
        return ["has no __all__"]
    problems = []
    for name in names:
        if name.startswith("_"):
            problems.append(f"{name!r} starts with an underscore")
        if not hasattr(module, name):
            problems.append(f"{name!r} is in __all__ but missing from the module")
    if len(set(names)) != len(names):
        problems.append("__all__ has duplicates")
    return problems


@pytest.mark.parametrize("name", sorted(STABLE))
def test_all_is_exactly_the_documented_surface(name: str) -> None:
    mod = importlib.import_module(name)
    assert check_all(mod) == []
    assert sorted(mod.__all__) == STABLE[name]
    assert "main" not in mod.__all__


def test_all_check_fails_on_a_missing_or_private_name() -> None:
    mod = ModuleType("synthetic")
    mod.public = 1  # type: ignore[attr-defined]
    mod._private = 2  # type: ignore[attr-defined]
    mod.__all__ = ["public", "gone", "_private"]  # type: ignore[attr-defined]
    problems = check_all(mod)
    assert any("'gone'" in p and "missing" in p for p in problems)
    assert any("'_private'" in p and "underscore" in p for p in problems)
    assert check_all(ModuleType("bare")) == ["has no __all__"]


def test_internal_names_are_not_exported() -> None:
    assert not hasattr(spec_edit, "__all__") or "_parse" not in spec_edit.__all__
    assert "main" not in spec_schema.__all__


# ---- item 2: the stability page ----


def test_page_has_the_stable_modules_section(page: str) -> None:
    section = _section(page, "Stable Python modules")
    for sub in (
        "What the promise covers",
        "What it does not cover",
        "Documented behaviours",
        "What counts as a break",
        "Deprecation",
    ):
        assert _section(section, sub, "###").strip()


@pytest.mark.parametrize("name", sorted(STABLE))
def test_page_lists_each_module_and_exactly_its_names(page: str, name: str) -> None:
    section = _section(page, "Stable Python modules")
    row = next((ln for ln in section.splitlines() if ln.startswith(f"| `{name}`")), None)
    assert row is not None, f"{name} is not in the table"
    listed = re.findall(r"`([A-Za-z_]\w*)`", row.split("|")[2])
    assert sorted(listed) == sorted(importlib.import_module(name).__all__)


def test_page_states_what_is_and_is_not_covered(page: str) -> None:
    covered = _section(_section(page, "Stable Python modules"), "What the promise covers", "###")
    for phrase in (
        "parameter names, order, kinds",
        "defaults",
        "return type",
        "field names, order, types",
        "frozen-ness",
        "base classes",
        "documented behaviours",
    ):
        assert phrase in covered, phrase
    excluded = _section(_section(page, "Stable Python modules"), "What it does not cover", "###")
    assert "Internal names" in excluded and "text of error messages" in excluded
    assert "SpecProblem.message" in excluded


def test_page_applies_the_deprecation_window(page: str) -> None:
    dep = _section(_section(page, "Stable Python modules"), "Deprecation", "###")
    assert "nothing is removed in 1.x" in dep and "DeprecationWarning" in dep
    assert "next major version" in dep


def test_generation_spec_page_links_to_the_promise() -> None:
    text = (ROOT / "docs" / "GENERATION_SPEC.md").read_text(encoding="utf-8")
    assert "API_STABILITY.md#stable-python-modules" in text


def test_contributing_explains_the_baseline() -> None:
    text = (ROOT / "docs" / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "stable_api_compat.py --write" in text and "new major version" in text


# ---- item 4: behaviour pins ----

SPEC = """{
  "schema_version": 1,
  "$comment": "owned by the data team",
  "x-owner": {"team": "data"},
  "model": {"name": "shop", "seed": 5, "x-ticket": "ABC-1"},
  "tables": {
    "orders": {
      "name": "orders",
      "primary_key": ["id"],
      "x-layout": {"x": 10},
      "columns": {
        "id": {"name": "id", "type": "integer", "generator": {"strategy": "sequence"}},
        "total": {
          "name": "total",
          "type": "float",
          "x-note": "kept",
          "generator": {"strategy": "uniform", "low": 1.0, "high": 9.0, "$comment": "range"}
        }
      }
    }
  },
  "generation": {"scale": "s", "scales": {"s": {"orders": 5}}}
}
"""


@pytest.mark.parametrize(
    "text",
    [SPEC, SPEC.rstrip("\n"), SPEC.replace("  ", "\t")],
    ids=["lf", "no-final-newline", "tabs"],
)
def test_unchanged_spec_is_written_back_byte_for_byte(text: str, tmp_path: Path) -> None:
    assert SpecDocument.loads(text).dumps() == text
    path = tmp_path / "s.json"
    path.write_bytes(text.encode("utf-8"))
    SpecDocument.load(path).save(path)
    assert path.read_bytes() == text.encode("utf-8")


def test_text_with_crlf_is_written_back_byte_for_byte_by_loads() -> None:
    text = SPEC.replace("\n", "\r\n")
    assert SpecDocument.loads(text).dumps() == text


def test_an_edit_keeps_unknown_keys_and_key_order() -> None:
    doc = SpecDocument.loads(SPEC)
    before = doc.to_dict()
    doc.set_generator("orders", "total", {"strategy": "uniform", "low": 2.0, "high": 3.0})
    doc.add_column(
        "orders", "note", "string", {"strategy": "constant", "value": "n"}, **{"x-ui": 1}
    )
    after = json.loads(doc.dumps())
    assert list(after) == list(before)
    assert after["$comment"] == "owned by the data team" and after["x-owner"] == {"team": "data"}
    assert after["model"]["x-ticket"] == "ABC-1"
    orders = after["tables"]["orders"]
    assert orders["x-layout"] == {"x": 10}
    assert list(orders) == list(before["tables"]["orders"])
    assert orders["columns"]["total"]["x-note"] == "kept"
    assert list(orders["columns"]) == ["id", "total", "note"]
    assert orders["columns"]["note"]["x-ui"] == 1
    # an edit that removes nothing never drops a key the edit did not touch
    assert set(before["tables"]["orders"]["columns"]["total"]) <= set(orders["columns"]["total"])


def test_a_key_a_newer_minor_adds_survives() -> None:
    doc = SpecDocument.loads(SPEC.replace('"x-owner"', '"future_field": [1, 2], "x-owner"'))
    doc.set("/model/seed", 9)
    out = json.loads(doc.dumps())
    assert out["future_field"] == [1, 2] and out["model"]["seed"] == 9


def test_every_problem_has_a_pointer_and_text_problems_have_a_place() -> None:
    bad = SPEC.replace('"strategy": "sequence"', '"strategy": "sequence", "typo_key": 1')
    bad = bad.replace('"low": 1.0, ', "")
    from_text = SpecDocument.loads(bad).validate()
    assert from_text
    for p in from_text:
        assert isinstance(p, SpecProblem)
        assert p.pointer.startswith("/") or p.pointer == ""
        assert p.line is not None and p.line >= 1 and p.column is not None and p.column >= 1
    from_dict = SpecDocument.from_dict(json.loads(bad)).validate()
    assert from_dict and all(p.pointer.startswith("/") or p.pointer == "" for p in from_dict)
    assert all(p.line is None and p.column is None for p in from_dict)
    for p in validate_text(bad):
        assert p.line is not None and p.column is not None


def test_published_schema_is_the_shipped_file() -> None:
    shipped = ROOT / "src" / "shape" / "schemas" / "generation-spec-v1.schema.json"
    assert published_schema() == json.loads(shipped.read_text(encoding="utf-8"))


def _test_ids(page: str) -> list[str]:
    behaviours = _section(_section(page, "Stable Python modules"), "Documented behaviours", "###")
    return re.findall(r"`(tests/[\w/]+\.py::\w+)`", behaviours)


def test_listed_tests_exist(page: str) -> None:
    ids = _test_ids(page)
    assert len(ids) >= 4
    for tid in ids:
        file, _, name = tid.partition("::")
        path = ROOT / file
        assert path.exists(), f"{tid}: file does not exist"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        assert name in defined, f"{tid}: no such test"


def test_every_documented_behaviour_has_a_listed_test(page: str) -> None:
    ids = set(_test_ids(page))
    here = "tests/api/test_stable_api_promise.py::"
    for name in (
        "test_unchanged_spec_is_written_back_byte_for_byte",
        "test_an_edit_keeps_unknown_keys_and_key_order",
        "test_every_problem_has_a_pointer_and_text_problems_have_a_place",
        "test_published_schema_is_the_shipped_file",
    ):
        assert here + name in ids, name
