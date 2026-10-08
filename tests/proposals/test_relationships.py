"""W1-02 deliverable 2: relationship inference over a multi-table profile."""

from __future__ import annotations

import pyarrow as pa
import pytest

import shape
from shape.proposals import propose, propose_relationships

from .conftest import shop_tables


def by_id(proposals):
    return {p.id: p for p in proposals}


CUST = "relationship:orders.customer_id->customers.customer_id"
PROD = "relationship:orders.product_id->products.product_id"


def test_real_foreign_keys_are_proposed_with_evidence_and_high_confidence(profile, tables):
    got = by_id(propose_relationships(profile, tables))
    for rid, parent, key in ((CUST, "customers", "customer_id"), (PROD, "products", "product_id")):
        p = got[rid]
        assert p.kind == "relationship" and p.confidence >= 0.9
        assert p.claim == {
            "child": "orders",
            "child_columns": [rid.split(".")[1].split("->")[0]],
            "parent": parent,
            "parent_columns": [key],
            "type": "one_to_many",
        }
        ev = p.evidence
        assert ev["name"]["score"] >= 0.9
        assert ev["containment"] == {
            "fraction": 1.0,
            "child_distinct": ev["containment"]["child_distinct"],
        }
        assert ev["type"]["compatible"] is True
        assert ev["range"]["within"] is True
        assert ev["cardinality"]["parent_unique"] is True
        assert ev["cardinality"]["child_distinct"] <= ev["cardinality"]["parent_distinct"]


def test_plural_table_names_are_matched_which_the_profiler_misses(profile, tables):
    # the multi-table profile itself found no relationship: customer_id vs the table "customers"
    assert profile.to_dict()["relationships"] == []
    assert CUST in by_id(propose_relationships(profile, tables))


def test_the_profilers_own_foreign_keys_are_reused_not_recomputed():
    t = {
        "customer": shop_tables()["customers"],
        "orders": shop_tables()["orders"].select(["order_id", "customer_id", "amount"]),
    }
    prof = shape.profile(t)
    assert prof.to_dict()["relationships"], "singular table name: the profiler detects it"
    got = by_id(propose_relationships(prof, t))
    p = got["relationship:orders.customer_id->customer.customer_id"]
    assert p.evidence["profiler_detected"] is True
    # not twice
    assert len([x for x in got if x.endswith("->customer.customer_id")]) == 1


def test_a_decoy_column_inside_the_key_range_is_not_proposed(profile, tables):
    ids = by_id(propose_relationships(profile, tables))
    assert not [i for i in ids if "status_code" in i]


def test_a_type_mismatch_is_not_proposed(profile, tables):
    ids = by_id(propose_relationships(profile, tables))
    assert not [i for i in ids if "customer_ref" in i]


def test_the_parent_column_must_be_unique(tables):
    t = dict(tables)
    t["customers"] = pa.table({"customer_id": [1, 1, 2, 2], "email": ["a", "b", "c", "d"]})
    ids = by_id(propose_relationships(shape.profile(t), t))
    assert CUST not in ids


def test_orphans_lower_the_confidence_and_heavy_orphans_drop_the_proposal():
    clean = shop_tables()
    some = shop_tables(orphans=40)  # 20% of the order rows point at no customer
    most = shop_tables(orphans=150)
    c = by_id(propose_relationships(shape.profile(clean), clean))[CUST]
    s = by_id(propose_relationships(shape.profile(some), some))[CUST]
    assert s.confidence < c.confidence
    assert s.evidence["containment"]["fraction"] < 1.0
    assert CUST not in by_id(propose_relationships(shape.profile(most), most))


def test_without_data_the_profile_alone_gives_a_proposal_with_less_confidence(profile, tables):
    with_data = by_id(propose_relationships(profile, tables))[CUST]
    alone = by_id(propose_relationships(profile))[CUST]
    assert "containment" not in alone.evidence and alone.evidence["range"]["within"] is True
    assert 0.5 <= alone.confidence < with_data.confidence


def test_a_name_free_candidate_needs_full_containment_and_many_distinct_values():
    t = shop_tables()
    t["orders"] = t["orders"].append_column("buyer", t["orders"]["customer_id"])
    prof = shape.profile(t)
    p = by_id(propose_relationships(prof, t))["relationship:orders.buyer->customers.customer_id"]
    assert p.evidence["name"]["score"] == 0.0 and 0.5 <= p.confidence < 0.8


def test_min_confidence_filters_and_is_validated(profile, tables):
    assert CUST in by_id(propose_relationships(profile, tables, min_confidence=1.0))  # inclusive
    some = shop_tables(orphans=40)
    got = propose_relationships(shape.profile(some), some)
    assert CUST in by_id(got)
    top = by_id(got)[CUST].confidence
    assert CUST not in by_id(
        propose_relationships(shape.profile(some), some, min_confidence=top + 0.01)
    )
    with pytest.raises(ValueError, match="min_confidence"):
        propose_relationships(profile, tables, min_confidence=1.5)


def test_a_single_table_profile_has_no_relationships():
    t = shop_tables()["customers"]
    assert propose_relationships(shape.profile(t)) == []


def test_empty_child_and_all_null_child_are_not_proposed():
    t = shop_tables()
    t["orders"] = t["orders"].set_column(
        t["orders"].schema.get_field_index("customer_id"),
        "customer_id",
        pa.array([None] * 200, type=pa.int64()),
    )
    assert CUST not in by_id(propose_relationships(shape.profile(t), t))


def test_proposals_are_deterministic_and_sorted_by_confidence_then_id(profile, tables):
    a = propose_relationships(profile, tables)
    b = propose_relationships(profile, tables)
    assert a == b
    keys = [(-p.confidence, p.id) for p in a]
    assert keys == sorted(keys)


def test_propose_runs_the_selected_kinds_only(profile, tables):
    kinds = {p.kind for p in propose(profile, tables, kinds=("relationship",))}
    assert kinds == {"relationship"}
    with pytest.raises(ValueError, match="kind"):
        propose(profile, tables, kinds=("telepathy",))


def test_data_for_an_unknown_table_is_an_error(profile, tables):
    with pytest.raises(ValueError, match="nope"):
        propose_relationships(profile, {**tables, "nope": tables["orders"]})


def test_data_that_disagrees_with_the_profile_is_an_error(profile, tables):
    bad = {**tables, "orders": tables["orders"].drop(["customer_id"])}
    with pytest.raises(ValueError, match="customer_id"):
        propose_relationships(profile, bad)
