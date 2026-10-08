"""W5-06 item 2: the Pydantic v2 importer (it runs the user's code, so it is opt-in)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from import_fixtures import FIXTURES, columns, generate

from shape.importers import import_schema
from shape.importers.core import ImportFormatError

pydantic = pytest.importorskip("pydantic", reason="Pydantic v2 is not installed")
MODELS = FIXTURES / "shop_models.py"


def test_a_named_model_with_nested_models_lists_and_enums() -> None:
    result = import_schema(f"{MODELS}:Order", "pydantic", allow_import=True)
    spec = result.spec.to_dict()
    assert {"Order", "Address", "Item", "Order_tags"} <= set(spec["tables"])
    cols = columns(result.spec, "Order")
    assert cols["color"]["generator"]["values"] == ["red", "blue"]
    assert cols["note"]["nullable"] is True and cols["note"]["generator"]["args"] == {
        "max_nb_chars": 25
    }
    assert cols["placed"]["type"] == "timestamp" and cols["token"]["type"] == "uuid"
    assert (cols["price"]["generator"]["min"], cols["price"]["generator"]["max"]) == (1.0, 50.0)
    assert columns(result.spec, "Item")["qty"]["generator"]["max"] == 9
    tables = generate(result.spec)
    assert set(tables["Item"].column("Order_id").to_pylist()) - {None} <= set(
        tables["Order"].column("id").to_pylist()
    )


def test_a_python_file_without_a_model_name_imports_every_model() -> None:
    result = import_schema(str(MODELS), allow_import=True)  # .py is inferred as pydantic
    assert {"Address", "Item", "Order"} <= set(result.spec.to_dict()["tables"])


def test_a_dotted_module_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(FIXTURES))
    try:
        result = import_schema("shop_models:Item", "pydantic", allow_import=True)
    finally:
        sys.modules.pop("shop_models", None)
    assert list(result.spec.to_dict()["tables"]) == ["Item"]


@pytest.mark.parametrize(
    ("target", "needle"),
    [
        (f"{MODELS}:Nope", "is not a Pydantic model"),
        (f"{MODELS}:Color", "is not a Pydantic model"),
        ("no_such_module_w506:X", "cannot import module"),
        (str(FIXTURES / "missing_models.py"), "file not found"),
    ],
)
def test_bad_targets_are_refused(target: str, needle: str) -> None:
    with pytest.raises(ImportFormatError, match=needle):
        import_schema(target, "pydantic", allow_import=True)


def test_a_module_that_fails_to_import_is_reported(tmp_path: Path) -> None:
    f = tmp_path / "broken_models.py"
    f.write_text("raise RuntimeError('boom')\n")
    with pytest.raises(ImportFormatError, match="RuntimeError: boom"):
        import_schema(str(f), allow_import=True)


def test_a_module_without_models_is_refused(tmp_path: Path) -> None:
    f = tmp_path / "plain.py"
    f.write_text("x = 1\n")
    with pytest.raises(ImportFormatError, match="defines no Pydantic model"):
        import_schema(str(f), allow_import=True)
