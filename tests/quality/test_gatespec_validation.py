"""#465: a gate schema value of the wrong type is a GateSchemaError, never coerced."""

from __future__ import annotations

import pytest

from shape.quality import GateSchema, GateSchemaError


def doc(table=None, relationships=None):
    out = {"format": "shape-gates", "version": 1, "tables": {"t": table or {"columns": {}}}}
    if relationships is not None:
        out["relationships"] = relationships
    return out


REL = {"name": "r", "parent": "p", "child": "c", "parent_columns": ["id"], "child_columns": ["pid"]}


@pytest.mark.parametrize(
    "table",
    [
        {"columns": ["a"]},
        {"columns": {"a": {"nullable": "false"}}},
        {"columns": {"a": {"nullable": 0}}},
        {"columns": {"a": {"type": 5}}},
        {"columns": {"a": {"enum": ["M", "F"]}}},
        {"columns": {"a": {"enum": {"M": "half"}}}},
        {"columns": {"a": {"enum": {"M": -1}}}},
        {"columns": {"a": {"distribution": "norm"}}},
        {"columns": {"a": {"distribution": {"params": {}}}}},
        {"columns": {"a": {"distribution": {"name": "norm", "params": [1]}}}},
        {"primary_key": "id", "columns": {}},
        {"primary_key": [1], "columns": {}},
    ],
)
def test_a_bad_table_value_is_refused(table):
    with pytest.raises(GateSchemaError):
        GateSchema.from_dict(doc(table))


@pytest.mark.parametrize(
    "rel",
    [
        {**REL, "parent_columns": "id"},
        {**REL, "child_columns": "pid"},
        {**REL, "parent_columns": []},
        {**REL, "child_columns": [1]},
    ],
)
def test_a_bad_relationship_value_is_refused(rel):
    with pytest.raises(GateSchemaError):
        GateSchema.from_dict(doc(relationships=[rel]))


def test_well_formed_values_still_load():
    s = GateSchema.from_dict(
        doc(
            {
                "primary_key": ["id"],
                "columns": {
                    "id": {"type": "integer", "nullable": False},
                    "g": {"nullable": True, "enum": {"M": 0.5, "F": 1}},
                    "x": {"distribution": {"name": "norm", "params": {"loc": 0}}},
                    "y": {"distribution": {"name": "norm"}, "enum": None},
                },
            },
            [REL],
        )
    )
    t = s.tables["t"]
    assert t.primary_key == ("id",) and t.columns["g"].nullable and not t.columns["id"].nullable
    assert s.relationships[0].parent_columns == ("id",)
