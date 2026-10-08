from __future__ import annotations

import copy
import json

import pytest
from gen_fixtures import schema

from shape.generation.schema import (
    GenSchema,
    GenSchemaError,
    json_schema,
    schema_problems,
)


def test_round_trip_is_exact():
    s = schema()
    doc = s.to_dict()
    assert GenSchema.from_dict(doc).to_dict() == doc
    assert json.loads(json.dumps(doc)) == doc
    assert doc["schema_version"] == 1


def test_document_follows_the_shipped_json_schema():
    assert schema_problems(schema().to_dict()) == []
    assert json_schema()["$id"].endswith("generation-schema-v1.json")


@pytest.mark.parametrize(
    "edit",
    [
        lambda d: d.pop("tables"),
        lambda d: d.update(schema_version=2),
        lambda d: d["model"].update(schema_mode="snowflake"),
        lambda d: d["tables"]["customer"]["columns"]["score"].update(null_rate=0.5, type=3),
        lambda d: d["tables"]["customer"].update(extra=1),
        lambda d: d["generation"]["scales"]["small"].update(customer=-1),
    ],
)
def test_malformed_documents_are_rejected(edit):
    doc = copy.deepcopy(schema().to_dict())
    edit(doc)
    assert schema_problems(doc)
    with pytest.raises(GenSchemaError):
        GenSchema.from_dict(doc)


def test_non_object_is_rejected():
    with pytest.raises(GenSchemaError):
        GenSchema.from_dict([])


def test_validate_reports_semantic_problems():
    s = schema()
    assert [i for i in s.validate() if i.level == "error"] == []
    s.tables["order"].primary_key.append("missing")
    s.tables["order"].columns["customer_id"].generator["ref"] = "ghost.id"
    s.tables["customer"].columns["score"].null_rate = 2.0
    s.tables["customer"].columns["name"].generator.pop("values")
    s.relationships[0].child_columns = ["nope"]
    msgs = " | ".join(i.message for i in s.validate())
    assert "Primary key column 'missing'" in msgs
    assert "non-existent table 'ghost'" in msgs
    assert "null_rate must be between 0 and 1" in msgs
    assert "Child column 'nope'" in msgs
    assert "expects key 'values'" in msgs
    with pytest.raises(GenSchemaError):
        s.validate_or_raise()


def test_properties():
    s = schema()
    assert s.tables["order"].fk_dependencies == {"customer"}
    assert s.tables["order"].columns["customer_id"].fk_ref_column == "customer_id"
    assert [r.name for r in s.get_children("customer")] == ["o_c"]
    assert [r.name for r in s.get_parents("order_line")] == ["l_o"]
    assert s.get_relationship("l_o") is not None and s.get_relationship("x") is None
