"""W5-05 item 1: a ``rename_column`` event for ``DriftPlan``."""

from __future__ import annotations

import pytest

import shape
from shape.errors import ShapeError
from shape.generation.drift_plan import DriftPlan, DriftPlanError
from shape.generation.schema import GenSchema


def _col(name, strategy="weighted_enum", **gen):
    gen = gen or {"values": {"a": 1, "b": 1}}
    return {"name": name, "type": "string", "generator": {"strategy": strategy, **gen}}


DOC = {
    "schema_version": 1,
    "model": {"name": "feed", "seed": 7, "schema_mode": "3nf"},
    "tables": {
        "customers": {
            "name": "customers",
            "primary_key": ["customer_id"],
            "columns": {
                "customer_id": {
                    "name": "customer_id",
                    "type": "integer",
                    "generator": {"strategy": "sequence"},
                },
                "segment": _col("segment", values={"x": 1, "y": 1}),
            },
        },
        "orders": {
            "name": "orders",
            "primary_key": ["order_id"],
            "columns": {
                "order_id": {
                    "name": "order_id",
                    "type": "integer",
                    "generator": {"strategy": "sequence"},
                },
                "customer_id": {
                    "name": "customer_id",
                    "type": "integer",
                    "generator": {"strategy": "foreign_key", "ref": "customers.customer_id"},
                },
                "status": _col("status", values={"open": 3, "done": 1}),
                "channel": _col("channel", values={"web": 3, "store": 1}),
            },
        },
    },
    "relationships": [
        {
            "name": "orders_customers",
            "parent": "customers",
            "child": "orders",
            "parent_columns": ["customer_id"],
            "child_columns": ["customer_id"],
        }
    ],
    "generation": {"scale": "small", "scales": {"small": {"customers": 30, "orders": 400}}},
}
SCHEMA = GenSchema.from_dict(DOC)

RENAME = {"kind": "rename_column", "column": "orders.status", "to": "order_status", "start": 3}


def plan(*events, days=10):
    return DriftPlan(list(events), start="2026-03-01", days=days)


def test_a_rename_is_on_or_off_from_its_start_day_and_keeps_the_column_position():
    p = plan(RENAME)
    before = p.schema_at(SCHEMA, 2).tables["orders"].columns
    after = p.schema_at(SCHEMA, 3).tables["orders"].columns
    assert list(before) == ["order_id", "customer_id", "status", "channel"]
    assert list(after) == ["order_id", "customer_id", "order_status", "channel"]
    assert after["order_status"].name == "order_status"
    assert after["order_status"].generator == before["status"].generator
    assert list(p.schema_at(SCHEMA, 9).tables["orders"].columns) == list(after)
    assert list(SCHEMA.tables["orders"].columns)[2] == "status"  # the input is not changed


def test_the_event_form_with_table_and_column_works_too():
    p = plan(
        {
            **{k: v for k, v in RENAME.items() if k != "column"},
            "table": "orders",
            "column": "status",
        }
    )
    assert "order_status" in p.schema_at(SCHEMA, 5).tables["orders"].columns


def test_a_rename_takes_no_ramp_and_may_end():
    with pytest.raises(DriftPlanError, match="no ramp"):
        plan({**RENAME, "ramp_days": 2})
    p = plan({**RENAME, "end": 6})
    assert "order_status" in p.schema_at(SCHEMA, 5).tables["orders"].columns
    assert p.schema_at(SCHEMA, 6).to_dict() == SCHEMA.to_dict()


def test_the_renamed_column_is_generated_under_the_new_name():
    p = plan(RENAME)
    old = p.generate_day(SCHEMA, 2, row_counts={"customers": 20, "orders": 50})["orders"]
    new = p.generate_day(SCHEMA, 3, row_counts={"customers": 20, "orders": 50})["orders"]
    assert "status" in old.column_names and "order_status" not in old.column_names
    assert new.column_names == ["order_id", "customer_id", "order_status", "channel"]


def test_an_unknown_column_or_table_names_the_column_and_the_new_name():
    for event, what in (
        ({**RENAME, "column": "orders.nope"}, "orders.nope"),
        ({**RENAME, "column": "ghosts.status"}, "ghosts.status"),
    ):
        with pytest.raises(ShapeError) as err:
            plan(event).schema_at(SCHEMA, 0)  # unknown targets fail on every day, even before it
        assert what in str(err.value) and "order_status" in str(err.value)


def test_a_rename_onto_an_existing_name_names_both():
    with pytest.raises(ShapeError) as err:
        plan({**RENAME, "to": "channel"}).schema_at(SCHEMA, 5)
    assert "orders.status" in str(err.value) and "channel" in str(err.value)
    with pytest.raises(ShapeError):  # also before the start day
        plan({**RENAME, "to": "channel"}).schema_at(SCHEMA, 0)


def test_a_rename_needs_a_new_name_and_a_different_one():
    with pytest.raises(DriftPlanError, match="'to'"):
        plan({"kind": "rename_column", "column": "orders.status", "start": 3})
    with pytest.raises(DriftPlanError, match="column name"):
        plan({**RENAME, "to": " "})
    with pytest.raises(DriftPlanError, match="same name"):
        plan({**RENAME, "to": "status"})
    with pytest.raises(DriftPlanError, match="needs"):
        plan({"kind": "rename_column", "column": "status", "to": "s", "start": 1})


def test_keys_relationships_and_references_follow_the_rename():
    p = plan(
        {"kind": "rename_column", "column": "customers.customer_id", "to": "cust_id", "start": 1}
    )
    s = p.schema_at(SCHEMA, 1)
    assert s.tables["customers"].primary_key == ["cust_id"]
    assert s.relationships[0].parent_columns == ["cust_id"]
    assert s.tables["orders"].columns["customer_id"].generator["ref"] == "customers.cust_id"
    assert s.relationships[0].child_columns == ["customer_id"]
    result = p.generate_day(SCHEMA, 1, row_counts={"customers": 20, "orders": 50})
    assert result.verify_integrity() == []
    assert "cust_id" in result["customers"].column_names


def test_a_column_that_another_generator_names_cannot_be_renamed():
    doc = {**DOC, "tables": {**DOC["tables"]}}
    orders = {**doc["tables"]["orders"], "columns": dict(doc["tables"]["orders"]["columns"])}
    orders["columns"]["double"] = {
        "name": "double",
        "type": "string",
        "generator": {"strategy": "computed", "expression": "status"},
    }
    doc["tables"]["orders"] = orders
    with pytest.raises(DriftPlanError, match="generator of orders.double"):
        plan(RENAME).schema_at(GenSchema.from_dict(doc), 4)


def test_the_answer_key_records_one_rename():
    p = plan(RENAME)
    truth = p.ground_truth()
    (event,) = truth["events"]
    assert event["kind"] == "rename_column" and event["spec"] == {"to": "order_status"}
    assert event["shape"] == "step"
    (expected,) = p.expected_changes(0, 9)
    assert expected["column"] == "status" and expected["kinds"] == ["column_removed"]
    assert expected["rename"] == {
        "from": "status",
        "to": "order_status",
        "added_kinds": ["column_added"],
    }
    assert p.expected_changes(0, 2) == []
    # a plan over more than one table qualifies the names, as for every event
    both = plan(
        RENAME,
        {"kind": "rename_column", "column": "customers.segment", "to": "tier", "start": 3},
    )
    first, _ = both.expected_changes(0, 9)
    assert first["column"] == "orders.status"
    assert first["rename"]["to"] == "orders.order_status"


def test_the_diff_sees_a_dropped_and_an_added_column():
    p = plan(RENAME)
    rows = {"customers": 30, "orders": 400}
    a = shape.profile(p.generate_day(SCHEMA, 0, row_counts=rows)["orders"], name="orders")
    b = shape.profile(p.generate_day(SCHEMA, 9, row_counts=rows)["orders"], name="orders")
    found = {(c["column"], c["kind"]) for c in shape.diff(a, b).changes}
    assert ("status", "column_removed") in found and ("order_status", "column_added") in found
    (expected,) = p.expected_changes(0, 9)
    assert (expected["column"], expected["kinds"][0]) in found
    assert (expected["rename"]["to"], expected["rename"]["added_kinds"][0]) in found
