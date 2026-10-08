"""W5-06 item 2: the OpenAPI 3.0 and 3.1 importer."""

from __future__ import annotations

from pathlib import Path

import pytest
from import_fixtures import FIXTURES, columns, generate

from shape.importers import import_schema
from shape.importers.core import ImportFormatError


def test_one_table_per_object_schema_and_refs_become_relationships() -> None:
    result = import_schema(FIXTURES / "petstore.openapi.json")
    spec = result.spec.to_dict()
    assert set(spec["tables"]) == {"Category", "Tag", "NewPet", "Pet"}
    rels = {(r["child"], r["parent"], tuple(r["child_columns"])) for r in spec["relationships"]}
    assert ("Pet", "Category", ("category_id",)) in rels
    # An array of $ref objects puts a nullable key on the referenced table.
    assert ("Tag", "Pet", ("Pet_id",)) in rels
    assert ("Tag", "NewPet", ("NewPet_id",)) in rels
    assert columns(result.spec, "Tag")["Pet_id"]["nullable"] is True


def test_allof_merges_the_branches_into_the_table() -> None:
    cols = columns(import_schema(FIXTURES / "petstore.openapi.json").spec, "Pet")
    assert {"id", "name", "price", "nickname"} <= set(cols)
    assert cols["id"]["generator"] == {"strategy": "sequence", "start": 1}
    assert cols["price"]["generator"]["max"] == 500
    assert cols["nickname"]["nullable"] is True  # OpenAPI 3.0 nullable: true
    assert cols["name"]["nullable"] is False
    assert cols["status"]["generator"] == {
        "strategy": "choice",
        "values": ["available", "pending", "sold"],
    }  # a $ref to an enum schema is that enum


def test_what_is_not_imported_is_reported() -> None:
    result = import_schema(FIXTURES / "petstore.openapi.json")
    skipped = {i["element"]: i["reason"] for i in result.report.not_imported}
    assert skipped["#/paths"].startswith("paths are not imported")
    assert "components.parameters" in skipped["#/components/parameters"]
    assert "oneOf with 2 non-null branches" in skipped["#/components/schemas/Shape"]
    assert "#/components/schemas/Status" in skipped  # an enum schema is not a table
    assert "Shape" not in result.spec.to_dict()["tables"]


def test_the_generated_data_respects_keys_and_bounds() -> None:
    tables = generate(import_schema(FIXTURES / "petstore.openapi.json").spec)
    prices = [p for p in tables["Pet"].column("price").to_pylist() if p is not None]
    assert min(prices) >= 1 and max(prices) <= 500
    categories = set(tables["Category"].column("id").to_pylist())
    assert set(tables["Pet"].column("category_id").to_pylist()) - {None} <= categories


def test_openapi_31_type_arrays_and_nullable_one_of() -> None:
    result = import_schema(FIXTURES / "library.openapi31.json")
    cols = columns(result.spec, "Book")
    assert cols["rating"]["nullable"] is True and cols["rating"]["generator"]["max"] == 5
    assert cols["editor_id"]["nullable"] is True
    assert cols["author_id"]["type"] == "uuid" and cols["author_id"]["nullable"] is False
    assert columns(result.spec, "Author")["born"]["type"] == "date"
    generate(result.spec)


def test_yaml_documents_are_read() -> None:
    pytest.importorskip("yaml", reason="PyYAML is not installed")
    result = import_schema(FIXTURES / "library.openapi.yaml")
    assert set(result.spec.to_dict()["tables"]) == {"Shelf", "Copy"}
    generate(result.spec)


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ('{"openapi": "2.0"}', "OpenAPI 2.0 is not supported"),
        ('{"openapi": "3.0.0", "components": {}}', "components.schemas is missing"),
        (
            '{"openapi":"3.0.0","components":{"schemas":{"A":{"type":"string"}}}}',
            "no object schema with properties",
        ),
        (
            '{"openapi":"3.0.0","components":{"schemas":{"A":{"type":"object","properties":'
            '{"b":{"$ref":"#/components/schemas/Nope"}}}}}}',
            "does not exist in the document",
        ),
    ],
)
def test_malformed_openapi_is_refused(tmp_path: Path, text: str, needle: str) -> None:
    f = tmp_path / "api.json"
    f.write_text(text)
    with pytest.raises(ImportFormatError, match=needle) as info:
        import_schema(f)
    assert info.value.file == str(f)


def test_swagger_two_is_not_guessed_as_openapi(tmp_path: Path) -> None:
    f = tmp_path / "old.json"
    f.write_text('{"swagger": "2.0", "info": {}}')
    with pytest.raises(ImportFormatError, match="Swagger 2.0"):
        import_schema(f)
