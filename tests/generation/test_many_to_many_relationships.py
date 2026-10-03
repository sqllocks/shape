"""A many-to-many relationship in a profile (``shape profile-model`` records them) is recorded
and is never a foreign key: generation, the fit plan and relationship proposals leave it out."""

from __future__ import annotations

import pyarrow as pa

import shape
from shape.generation.fit import fit_schema
from shape.generation.learn import learn
from shape.profile.reference import Profile
from shape.proposals import propose_relationships


def _profile(kind: str):
    tables = {
        "A": pa.table({"Id": [1, 2, 3], "Label": ["x", "y", "z"]}),
        "B": pa.table({"Id": [1, 2, 3, 4], "AId": [1, 1, 2, 3]}),
    }
    data = shape.profile(tables).to_dict()
    data["relationships"] = [
        {
            "name": "fk_B_AId",
            "parent": "A",
            "child": "B",
            "parent_columns": ["Id"],
            "child_columns": ["AId"],
            "type": kind,
            "evidence": "declared",
        }
    ]
    return Profile(data, name="m")


def test_a_one_to_many_relationship_is_generated():
    schema = learn(_profile("one_to_many"))
    assert [r.name for r in schema.relationships] == ["fk_B_AId"]
    plan = [i.evidence for i in fit_schema(_profile("one_to_many")).plan.items]
    assert "relationship:fk_B_AId" in plan


def test_a_many_to_many_relationship_is_left_out_of_generation_and_the_plan():
    assert learn(_profile("many_to_many")).relationships == []
    plan = [i.evidence for i in fit_schema(_profile("many_to_many")).plan.items]
    assert "relationship:fk_B_AId" not in plan


def test_a_many_to_many_relationship_is_not_proposed_as_detected():
    plain = {p.id: p for p in propose_relationships(_profile("one_to_many"), min_confidence=0.0)}
    assert plain["relationship:B.AId->A.Id"].evidence["profiler_detected"] is True
    m2m = {p.id: p for p in propose_relationships(_profile("many_to_many"), min_confidence=0.0)}
    found = m2m.get("relationship:B.AId->A.Id")
    assert found is None or "profiler_detected" not in found.evidence
