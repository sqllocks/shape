"""W5-06 items 1-3: the JSON Schema importer (draft 2020-12 and draft 7)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from import_fixtures import FIXTURES, columns, generate

from shape.importers import import_schema
from shape.importers.core import ImportFormatError


def _skipped(result: Any) -> dict[str, str]:
    return {i["element"]: i["reason"] for i in result.report.not_imported}


def test_the_order_schema_becomes_tables_with_keys() -> None:
    result = import_schema(FIXTURES / "order.schema.json")
    spec = result.spec.to_dict()
    assert list(spec["tables"]) == [
        "order",
        "customer",
        "order_shipping",
        "order_items",
        "order_tags",
    ]
    assert spec["tables"]["order"]["primary_key"] == ["order_id"]
    rels = {(r["child"], r["parent"], tuple(r["child_columns"])) for r in spec["relationships"]}
    assert ("order", "customer", ("customer_id",)) in rels
    assert ("order_shipping", "order", ("order_id",)) in rels
    assert ("order_items", "order", ("order_id",)) in rels
    assert ("order_tags", "order", ("order_id",)) in rels
    # An unreferenced definition is listed, not imported.
    assert _skipped(result)["#/$defs/unused"] == "not referenced from the root schema"


def test_strategies_follow_the_declared_constraints() -> None:
    cols = columns(import_schema(FIXTURES / "order.schema.json").spec, "order")
    assert cols["order_id"]["generator"] == {"strategy": "sequence", "start": 1}
    assert cols["status"]["generator"] == {
        "strategy": "choice",
        "values": ["new", "paid", "shipped"],
    }
    q = cols["quantity"]["generator"]
    assert (q["strategy"], q["distribution"], q["min"], q["max"]) == (
        "distribution",
        "uniform",
        1,
        20,
    )
    p = cols["unit_price"]["generator"]
    assert (p["min"], p["max"]) == (0.5, 99.5)
    assert cols["code"]["generator"] == {"strategy": "pattern", "format": "{random:6}"}
    assert cols["note"]["generator"]["args"] == {"max_nb_chars": 30}
    assert cols["note"]["max_length"] == 30
    assert cols["email"]["generator"] == {"strategy": "faker", "provider": "email"}
    assert cols["token"]["type"] == "uuid"
    assert cols["token"]["generator"] == {"strategy": "uuid"}
    assert cols["placed_at"]["type"] == "timestamp"
    assert cols["placed_at"]["generator"]["strategy"] == "temporal"
    assert cols["shipped_on"]["type"] == "date"
    assert cols["express"]["generator"]["strategy"] == "weighted_enum"
    assert cols["channel"]["generator"] == {"strategy": "constant", "value": "web"}


def test_nullability_follows_required_and_null_types() -> None:
    cols = columns(import_schema(FIXTURES / "order.schema.json").spec, "order")
    assert cols["quantity"]["nullable"] is False and cols["quantity"]["null_rate"] == 0.0
    assert cols["note"]["nullable"] is True and cols["note"]["null_rate"] > 0
    assert cols["shipped_on"]["nullable"] is True  # ["string", "null"]
    assert cols["order_id"]["nullable"] is False  # the key


def test_nested_object_array_and_scalar_array_children() -> None:
    spec = import_schema(FIXTURES / "order.schema.json").spec
    assert set(columns(spec, "order_shipping")) == {"id", "order_id", "city", "zip"}
    assert columns(spec, "order_shipping")["order_id"]["generator"] == {
        "strategy": "foreign_key",
        "ref": "order.order_id",
        "distribution": "pareto",
    }
    assert set(columns(spec, "order_items")) == {"id", "order_id", "sku", "qty"}
    assert set(columns(spec, "order_tags")) == {"id", "order_id", "value"}
    assert columns(spec, "order_tags")["value"]["generator"]["values"] == ["gift", "fragile"]


def test_the_imported_spec_generates_with_valid_keys() -> None:
    tables = generate(import_schema(FIXTURES / "order.schema.json").spec)
    customers = set(tables["customer"].column("customer_id").to_pylist())
    assert set(tables["order"].column("customer_id").to_pylist()) <= customers
    orders = set(tables["order"].column("order_id").to_pylist())
    assert set(tables["order_items"].column("order_id").to_pylist()) <= orders
    quantities = tables["order"].column("quantity").to_pylist()
    assert min(quantities) >= 1 and max(quantities) <= 20
    assert set(tables["order"].column("status").to_pylist()) <= {"new", "paid", "shipped"}


def test_what_cannot_be_imported_is_reported_with_the_reason() -> None:
    result = import_schema(FIXTURES / "refused.schema.json")
    skipped = _skipped(result)
    assert skipped["#/properties/mixed"] == "oneOf with 3 non-null branches: imported as string"
    assert "leaves the document" in skipped["#/properties/remote"]
    assert skipped["#/properties/freeform"] == "object without properties: imported as string"
    assert "array of arrays" in skipped["#/properties/grid"]
    assert "multipleOf" in {i["kind"] for i in result.report.not_imported}
    assert "pattern" in {i["kind"] for i in result.report.not_imported}
    assert skipped["#/properties/kind"].startswith("type ['string', 'integer'] has several")
    # anyOf with one non-null branch is that branch, nullable, with its constraints.
    maybe = columns(result.spec, "refused")["maybe"]
    assert maybe["nullable"] is True and maybe["generator"]["args"] == {"max_nb_chars": 12}
    # The refused elements still give a column, and the spec generates.
    generate(result.spec)


def test_exclusive_bounds_on_an_integer_become_inclusive_bounds() -> None:
    gen = columns(import_schema(FIXTURES / "refused.schema.json").spec, "refused")["upto"][
        "generator"
    ]
    assert (gen["min"], gen["max"]) == (1, 9)


def test_draft_7_definitions_refs_and_recursion() -> None:
    result = import_schema(FIXTURES / "library.draft7.json")
    spec = result.spec.to_dict()
    assert list(spec["tables"]) == ["author", "book"]
    book = spec["tables"]["book"]["columns"]
    assert book["author_id"]["generator"]["ref"] == "author.author_id"
    assert book["author_id"]["type"] == "uuid"  # follows the key it points at
    assert book["sequel_id"]["generator"]["strategy"] == "self_referencing"
    assert spec["tables"]["author"]["columns"]["author_id"]["generator"] == {"strategy": "uuid"}
    generate(result.spec)


def test_a_root_array_of_objects_is_one_table(tmp_path: Path) -> None:
    f = tmp_path / "rows.json"
    f.write_text(
        '{"type":"array","items":{"type":"object","properties":{"id":{"type":"integer"}}}}'
    )
    assert list(import_schema(f).spec.to_dict()["tables"]) == ["rows"]


@pytest.mark.parametrize(
    ("text", "line", "needle"),
    [
        ('{\n  "type": "object",\n  "properties": {,}\n}\n', 3, "not valid JSON"),
        ("[1, 2]", 1, "must be an object"),
        (
            '{"type":"object","properties":{\n"a":{"$ref":"#/$defs/missing"}}}',
            2,
            "does not exist in the document",
        ),
        ('{"type":"object","properties":{"a":{"type":"string","maxLength":0}}}', 1, "maxLength"),
        (
            '{"type":"object","properties":{"a":{"type":"integer","minimum":5,"maximum":1}}}',
            None,
            "minimum 5 is greater than maximum 1",
        ),
        ('{"type":"object","properties":{"a":{"type":"string","pattern":"("}}}', 1, "pattern"),
        ('{"type":"object","properties":{"a":{"enum":[]}}}', 1, "enum"),
        ('{"type":"string"}', None, "not an object with properties"),
    ],
)
def test_malformed_input_names_the_file_line_and_element(
    tmp_path: Path, text: str, line: int | None, needle: str
) -> None:
    f = tmp_path / "bad.json"
    f.write_text(text)
    with pytest.raises(ImportFormatError) as info:
        import_schema(f, "jsonschema")
    exc = info.value
    assert needle in str(exc)
    assert exc.file == str(f)
    if line is not None:
        assert exc.line == line
    if "does not exist" in needle:
        assert exc.element == "#/properties/a"


def test_a_circular_ref_chain_is_refused(tmp_path: Path) -> None:
    f = tmp_path / "loop.json"
    f.write_text(
        '{"type":"object","properties":{"a":{"$ref":"#/$defs/x"}},'
        '"$defs":{"x":{"$ref":"#/$defs/y"},"y":{"$ref":"#/$defs/x"}}}'
    )
    with pytest.raises(ImportFormatError, match="circular"):
        import_schema(f)


def test_the_report_is_deterministic_and_lists_every_column() -> None:
    a = import_schema(FIXTURES / "order.schema.json")
    b = import_schema(FIXTURES / "order.schema.json")
    assert a.report.dumps() == b.report.dumps()
    assert a.spec.dumps() == b.spec.dumps()
    imported = {i["element"] for i in a.report.imported}
    assert "#/properties/quantity" in imported and "#/properties/items" in imported


def test_mutually_referencing_schemas_are_generated_by_breaking_the_cycle(tmp_path: Path) -> None:
    f = tmp_path / "pair.json"
    f.write_text(
        '{"title":"a","type":"object","required":["a_id"],"properties":{"a_id":{"type":"integer"},'
        '"b":{"$ref":"#/$defs/b"}},"$defs":{"b":{"type":"object","required":["b_id"],'
        '"properties":{"b_id":{"type":"integer"},"a":{"$ref":"#"}}}}}'
    )
    result = import_schema(f)
    kinds = [i for i in result.report.not_imported if i["kind"] == "foreign key"]
    assert len(kinds) == 1 and "closes a cycle" in kinds[0]["reason"]
    assert len(result.spec.to_dict()["relationships"]) == 1
    generate(result.spec)
