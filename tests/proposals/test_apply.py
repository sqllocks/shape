"""W1-02 deliverable 4: decisions are applied by later runs; rejected relationships stay out,
accepted ones are kept by generation from the profile."""

from __future__ import annotations

import pytest

import shape
from shape.generation.fit import fit_schema
from shape.proposals import DecisionError, DecisionFile, apply_decisions, propose

from .conftest import NOW
from .test_relationships import CUST, PROD


def decided(profile, tables, **statuses):
    f = DecisionFile.empty()
    f.update(propose(profile, tables), now=NOW)
    for pid, status in statuses.items():
        f.decide(pid.replace("__", ":").replace("_to_", "->"), status, actor="ana", now=NOW)
    return f


def fk_refs(schema):
    return {
        (t, c): col.generator.get("ref")
        for t, tbl in schema.tables.items()
        for c, col in tbl.columns.items()
        if col.generator.get("strategy") == "foreign_key"
    }


def test_without_the_decision_generation_has_no_relationship(profile):
    assert fit_schema(profile).schema.relationships == []


def test_an_accepted_relationship_is_kept_by_generation(profile, tables):
    f = DecisionFile.empty()
    f.update(propose(profile, tables), now=NOW)
    f.decide(CUST, "accepted", actor="ana", now=NOW)
    applied = apply_decisions(profile, f).to_dict()
    assert applied["relationships"] == [
        {
            "name": "fk_orders_customer_id",
            "parent": "customers",
            "child": "orders",
            "parent_columns": ["customer_id"],
            "child_columns": ["customer_id"],
            "type": "one_to_many",
        }
    ]
    col = applied["tables"]["orders"]["columns"]["customer_id"]
    assert col["is_foreign_key"] is True and col["fk_ref_table"] == "customers"
    assert applied["tables"]["orders"]["detected_fks"] == {"customer_id": "customers"}

    schema = fit_schema(profile, decisions=f).schema
    assert [(r.parent, r.child) for r in schema.relationships] == [("customers", "orders")]
    assert fk_refs(schema)[("orders", "customer_id")] == "customers.customer_id"
    assert ("orders", "product_id") not in fk_refs(schema)  # undecided is not applied


def test_pending_and_deferred_proposals_change_nothing(profile, tables):
    f = DecisionFile.empty()
    f.update(propose(profile, tables), now=NOW)
    f.decide(PROD, "deferred", actor="ana", now=NOW)
    assert apply_decisions(profile, f).to_dict() == profile.to_dict()


def test_a_rejected_relationship_is_removed_from_a_profile_that_detected_it():
    from .conftest import shop_tables

    t = {"customer": shop_tables()["customers"], "orders": shop_tables()["orders"]}
    prof = shape.profile(t)
    rid = "relationship:orders.customer_id->customer.customer_id"
    assert prof.to_dict()["relationships"]
    f = DecisionFile.empty()
    f.update(propose(prof, t), now=NOW)
    f.decide(rid, "rejected", actor="ana", note="a code, not a key", now=NOW)
    out = apply_decisions(prof, f).to_dict()
    assert out["relationships"] == []
    assert out["tables"]["orders"]["detected_fks"] == {}
    assert out["tables"]["orders"]["columns"]["customer_id"]["is_foreign_key"] is False
    assert fit_schema(prof, decisions=f).schema.relationships == []


def test_apply_does_not_change_the_input_profile(profile, tables):
    before = profile.to_dict()
    f = decided(profile, tables)
    f.decide(CUST, "accepted", actor="a", now=NOW)
    apply_decisions(profile, f)
    assert profile.to_dict() == before


def test_applying_twice_is_the_same_as_once(profile, tables):
    f = DecisionFile.empty()
    f.update(propose(profile, tables), now=NOW)
    f.decide(CUST, "accepted", actor="a", now=NOW)
    once = apply_decisions(profile, f)
    assert apply_decisions(once, f).to_dict() == once.to_dict()


def test_an_accepted_relationship_to_a_table_the_profile_lacks_is_an_error(tables):
    prof = shape.profile(tables)
    f = DecisionFile.empty()
    f.update(propose(prof, tables), now=NOW)
    f.decide(CUST, "accepted", actor="a", now=NOW)
    smaller = shape.profile({k: v for k, v in tables.items() if k != "customers"})
    with pytest.raises(DecisionError, match="customers"):
        apply_decisions(smaller, f)


def test_the_accepted_relationship_survives_a_reprofile_without_re_asking(profile, tables):
    f = DecisionFile.empty()
    f.update(propose(profile, tables), now=NOW)
    f.decide(CUST, "accepted", actor="a", now=NOW)
    again = shape.profile(tables)  # a fresh profile does not know the decision
    assert fit_schema(again).schema.relationships == []
    assert len(fit_schema(again, decisions=f).schema.relationships) == 1
    assert f.list(status="pending")  # the others still wait for a person; nothing auto-accepted
