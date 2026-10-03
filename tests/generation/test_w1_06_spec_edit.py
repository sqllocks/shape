"""W1-06 (#64) deliverables 3 and 4: load, validate, edit and save a generation spec without
losing unknown optional fields, and validation errors that point at the exact place."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shape.generation.engine import Engine
from shape.generation.spec_edit import SpecDocument, SpecError, SpecProblem

TEXT = """{
  "schema_version": 1,
  "$comment": "owned by the data team",
  "x-owner": {"team": "data", "tags": ["a", "b"]},
  "model": {"name": "shop", "seed": 5, "x-ticket": "ABC-1"},
  "tables": {
    "orders": {
      "name": "orders",
      "primary_key": ["id"],
      "x-layout": {"x": 10, "y": 20},
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


def doc() -> SpecDocument:
    return SpecDocument.loads(TEXT)


def test_an_unchanged_spec_is_written_back_byte_for_byte(tmp_path: Path) -> None:
    d = doc()
    assert d.dumps() == TEXT
    path = tmp_path / "spec.json"
    path.write_text(TEXT, encoding="utf-8")
    loaded = SpecDocument.load(path)
    loaded.save(path)
    assert path.read_text("utf-8") == TEXT


def test_unknown_optional_fields_survive_an_edit_and_a_round_trip(tmp_path: Path) -> None:
    d = doc()
    d.set_generator("orders", "total", {"strategy": "normal", "mean": 5.0, "stddev": 1.0})
    d.add_column("orders", "note", "string", {"strategy": "constant", "value": "n"}, **{"x-ui": 1})
    path = tmp_path / "out.json"
    d.save(path)
    back = json.loads(path.read_text("utf-8"))
    assert back["$comment"] == "owned by the data team"
    assert back["x-owner"] == {"team": "data", "tags": ["a", "b"]}
    assert back["model"]["x-ticket"] == "ABC-1"
    assert back["tables"]["orders"]["x-layout"] == {"x": 10, "y": 20}
    assert back["tables"]["orders"]["columns"]["total"]["x-note"] == "kept"
    assert back["tables"]["orders"]["columns"]["note"]["x-ui"] == 1
    assert list(back["tables"]["orders"]["columns"]) == ["id", "total", "note"]  # order kept
    assert SpecDocument.load(path).validate() == []


def test_editing_one_column_changes_only_that_column() -> None:
    d = doc()
    before = d.to_dict()
    d.set_generator("orders", "total", {"strategy": "constant", "value": 1.0})
    after = d.to_dict()
    assert after["tables"]["orders"]["columns"]["total"]["generator"] == {
        "strategy": "constant",
        "value": 1.0,
    }
    after["tables"]["orders"]["columns"]["total"]["generator"] = before["tables"]["orders"][
        "columns"
    ]["total"]["generator"]
    assert after == before


def test_to_dict_is_a_copy() -> None:
    d = doc()
    d.to_dict()["model"]["name"] = "changed"
    assert d.get("/model/name") == "shop"


def test_generic_pointer_access() -> None:
    d = doc()
    assert d.get("/tables/orders/columns/id/type") == "integer"
    d.set("/model/seed", 9)
    assert d.get("/model/seed") == 9
    d.set("/model/x-new", {"k": [1]})
    assert d.to_dict()["model"]["x-new"] == {"k": [1]}
    d.remove("/model/x-new")
    assert "x-new" not in d.to_dict()["model"]
    with pytest.raises(KeyError, match="/model/missing"):
        d.get("/model/missing")
    with pytest.raises(KeyError):
        d.set("/nope/deeper", 1)
    with pytest.raises(ValueError, match="pointer"):
        d.get("model/seed")


def test_tables_and_columns_can_be_added_removed_and_listed() -> None:
    d = doc()
    d.add_table("items", primary_key=["id"], description="lines")
    d.add_column("items", "id", "integer", {"strategy": "sequence"})
    d.add_column(
        "items",
        "order_id",
        "integer",
        {"strategy": "foreign_key", "ref": "orders.id"},
        nullable=True,
    )
    assert d.table_names == ["orders", "items"]
    assert d.column_names("items") == ["id", "order_id"]
    assert d.validate() == []
    d.remove_column("items", "order_id")
    d.remove_table("items")
    assert d.table_names == ["orders"]
    with pytest.raises(KeyError, match="orders"):
        d.add_table("orders")
    with pytest.raises(KeyError, match="nope"):
        d.remove_column("orders", "nope")
    with pytest.raises(KeyError, match="nope"):
        d.set_generator("nope", "id", {})


def test_the_edited_spec_generates() -> None:
    d = doc()
    d.set_generator("orders", "total", {"strategy": "constant", "value": 3.5})
    schema = d.to_schema()
    table = Engine(schema, seed=1).generate().tables["orders"]
    assert table["total"].to_pylist() == [3.5] * 5


def test_a_dropped_field_would_be_noticed() -> None:
    """``GenSchema.to_dict`` drops extension keys; ``SpecDocument`` must not."""
    d = doc()
    assert "x-owner" not in d.to_schema().to_dict()
    assert "x-owner" in d.to_dict()


# ---- errors with locations -----------------------------------------------------------------


def line_of(text: str, needle: str) -> int:
    return next(i for i, line in enumerate(text.splitlines(), 1) if needle in line)


def test_a_typo_is_reported_at_the_key_with_line_column_and_a_hint() -> None:
    text = TEXT.replace('"low": 1.0', '"lw": 1.0')
    problems = SpecDocument.loads(text).validate()
    typo = [p for p in problems if "lw" in p.message]
    assert len(typo) == 1
    p = typo[0]
    assert p.level == "error"
    assert p.pointer == "/tables/orders/columns/total/generator/lw"
    assert p.path == "tables.orders.columns.total.generator.lw"
    assert p.line == line_of(text, '"lw"')
    assert p.column == text.splitlines()[p.line - 1].index('"lw"') + 1
    assert "did you mean 'low'" in p.message
    assert str(p).startswith(f"line {p.line}, column {p.column}: /tables/orders/columns/total")


def test_a_column_property_in_a_generator_says_so() -> None:
    text = TEXT.replace('"low": 1.0', '"scale": 2')
    msgs = [p.message for p in SpecDocument.loads(text).validate()]
    assert any("'scale'" in m and "column property" in m for m in msgs)


def test_a_missing_required_key_points_at_the_generator() -> None:
    text = TEXT.replace(
        '"strategy": "uniform", "low": 1.0, "high": 9.0, ', '"strategy": "uniform", "low": 1.0, '
    )
    text = text.replace('"strategy": "uniform"', '"strategy": "constant"')
    problems = SpecDocument.loads(text).validate()
    p = next(p for p in problems if "missing required key 'value'" in p.message)
    assert p.pointer == "/tables/orders/columns/total/generator"
    assert p.line == line_of(text, '"strategy": "constant"')


def test_a_wrong_type_points_at_the_value() -> None:
    text = TEXT.replace('"seed": 5', '"seed": "five"')
    p = SpecDocument.loads(text).validate()[0]
    assert p.pointer == "/model/seed" and p.line == line_of(text, '"seed": "five"')
    assert "expected ['integer']" in p.message


def test_semantic_errors_carry_locations_too() -> None:
    d = doc()
    d.add_column("orders", "cust", "integer", {"strategy": "foreign_key", "ref": "customers.id"})
    p = next(p for p in d.validate() if "non-existent table" in p.message)
    assert p.level == "error"
    assert p.pointer == "/tables/orders/columns/cust"
    d.set("/tables/orders/primary_key", ["missing"])
    assert any(
        p.pointer == "/tables/orders/primary_key" and p.level == "error" for p in d.validate()
    )


def test_a_semantic_warning_is_a_warning() -> None:
    d = doc()
    d.set("/tables/orders/primary_key", [])
    found = d.validate()
    assert [p.level for p in found] == ["warning"]
    d.raise_for_errors()  # warnings do not raise


def test_raise_for_errors_lists_every_error_with_its_place() -> None:
    d = doc()
    d.set("/tables/orders/columns/total/generator/oops", 1)
    d.set("/model/seed", "x")
    with pytest.raises(SpecError) as info:
        d.raise_for_errors()
    assert len(info.value.problems) == 2
    assert "/model/seed" in str(info.value) and "/generator/oops" in str(info.value)
    assert all(isinstance(p, SpecProblem) for p in info.value.problems)


def test_positions_are_known_only_for_a_loaded_text() -> None:
    d = SpecDocument.from_dict(json.loads(TEXT))
    d.set("/model/seed", "x")
    p = d.validate()[0]
    assert p.pointer == "/model/seed" and p.line is None and p.column is None
    assert str(p).startswith("/model/seed")


def test_an_edit_drops_stale_positions() -> None:
    d = doc()
    d.set("/model/seed", "x")
    assert d.validate()[0].line is None


@pytest.mark.parametrize(
    ("text", "line", "fragment"),
    [
        ('{\n  "a": 1,\n  "a": 2\n}', 3, "duplicate key 'a'"),
        ('{\n  "a": \n}', 3, "Expecting value"),
        ("[1, 2]", 1, "must be an object"),
        ("", 1, "Expecting value"),
    ],
)
def test_a_file_that_is_not_json_names_the_line(text: str, line: int, fragment: str) -> None:
    with pytest.raises(SpecError) as info:
        SpecDocument.loads(text)
    assert fragment in str(info.value)
    assert info.value.problems[0].line == line


def test_a_missing_file_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(SpecError, match="nope.json"):
        SpecDocument.load(tmp_path / "nope.json")


def test_save_is_atomic_and_leaves_no_temp_file(tmp_path: Path) -> None:
    d = doc()
    d.set("/model/seed", 6)
    path = tmp_path / "s.json"
    d.save(path)
    assert [p.name for p in tmp_path.iterdir()] == ["s.json"]
    assert json.loads(path.read_text("utf-8"))["model"]["seed"] == 6


def test_unicode_and_large_ints_round_trip(tmp_path: Path) -> None:
    d = doc()
    d.set("/model/description", "Café — ünïcode")
    d.set("/model/seed", 2**62)
    path = tmp_path / "u.json"
    d.save(path)
    assert "Café — ünïcode" in path.read_text("utf-8")
    assert SpecDocument.load(path).get("/model/seed") == 2**62


def test_validate_text_gives_positions_without_building_a_document() -> None:
    from shape.generation.spec_edit import validate_text

    assert validate_text(TEXT) == []
    bad = validate_text(TEXT.replace('"seed": 5', '"seed": true'))
    assert bad and bad[0].line == line_of(TEXT, '"seed": 5')
    assert validate_text("{") and validate_text("{")[0].level == "error"


def test_pointer_escapes() -> None:
    d = doc()
    d.add_table("a/b")
    d.add_column("a/b", "c~d", "string", {"strategy": "uuid"})
    assert d.get("/tables/a~1b/columns/c~0d/type") == "string"
    bad = [p for p in d.validate() if p.level == "error"]
    assert [p.pointer for p in bad] == ["/tables/a~1b"]  # a table name must be a plain name
