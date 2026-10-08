"""W3-01: ``shape.rules.mutation_test`` and ``shape rules mutate``."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pytest

from shape.rules import mutation_test
from shape.rules.mutation import MutationError


def orders(n: int = 400) -> pa.Table:
    rng = np.random.default_rng(3)
    base = dt.date(2026, 1, 1)
    return pa.table(
        {
            "order_id": pa.array(range(1, n + 1), type=pa.int64()),
            "customer_id": pa.array(rng.integers(1, 40, n), type=pa.int64()),
            "status": pa.array(rng.choice(["new", "paid", "shipped"], n).tolist()),
            "amount": pa.array((rng.random(n) * 90 + 10).round(2)),
            "created": pa.array([base + dt.timedelta(days=int(i % 28)) for i in range(n)]),
            "note": pa.array([f"note {i}" for i in range(n)]),
        }
    )


STRICT: dict[str, Any] = {
    "columns": {
        "order_id": {"unique": True, "nullable": False, "dtype": "integer"},
        "status": {"allowed_values": ["new", "paid", "shipped"]},
        "amount": {"min": 0, "nullable": False},
    }
}
NO_UNIQUE: dict[str, Any] = {
    "columns": {
        "order_id": {"nullable": False, "dtype": "integer"},
        "status": {"allowed_values": ["new", "paid", "shipped"]},
        "amount": {"min": 0, "nullable": False},
    }
}


def by_id(result: Any) -> dict[str, dict[str, Any]]:
    return {m["id"]: m for m in result.to_dict()["mutants"]}


def test_unique_rule_kills_the_duplicates_mutant_and_its_absence_does_not() -> None:
    kept = by_id(mutation_test({"orders": orders()}, STRICT, rate=0.05, seed=1))
    dropped = by_id(mutation_test({"orders": orders()}, NO_UNIQUE, rate=0.05, seed=1))
    assert kept["duplicates.orders"]["killed"] is True
    assert "orders.order_id.unique" in kept["duplicates.orders"]["killed_by"]
    assert dropped["duplicates.orders"]["killed"] is False
    assert dropped["duplicates.orders"]["status"] == "survived"
    assert dropped["duplicates.orders"]["killed_by"] == []


def test_empty_contract_scores_zero() -> None:
    result = mutation_test({"orders": orders()}, {}, seed=2)
    score = result.to_dict()["score"]
    assert score["overall"]["killed"] == 0
    assert score["overall"]["applicable"] > 0
    assert score["overall"]["score"] == 0.0
    assert result.to_dict()["rules_killed_none"] == []


def test_a_mutant_that_changes_no_cell_is_not_applicable_and_outside_the_score() -> None:
    flat = orders().set_column(3, "amount", pa.array([0.0] * 400))
    plan = {
        "format": "shape-mutation-plan",
        "version": 1,
        "corruptions": [{"kind": "negative_amounts", "table": "orders", "column": "amount"}],
    }
    result = mutation_test({"orders": flat}, STRICT, plan=plan)
    (m,) = result.to_dict()["mutants"]
    assert m["status"] == "not_applicable" and m["cells_changed"] == 0 and m["killed"] is False
    assert result.to_dict()["score"]["overall"] == {"killed": 0, "applicable": 0, "score": None}


def test_report_fields_and_scores_by_kind_and_table() -> None:
    result = mutation_test({"orders": orders()}, STRICT, seed=4, rate=0.1)
    doc = result.to_dict()
    assert doc["format"] == "shape-mutation-report" and doc["version"] == 1
    for m in doc["mutants"]:
        assert {"id", "kind", "table", "column", "rate", "seed", "cells_changed"} <= set(m)
        assert m["seed"] == 4 and m["rate"] == 0.1
        assert m["killed"] == bool(m["killed_by"])
    kinds = {m["kind"] for m in doc["mutants"]}
    assert set(doc["score"]["by_kind"]) == kinds
    assert set(doc["score"]["by_table"]) == {"orders"}
    overall = doc["score"]["overall"]
    assert overall["score"] == round(overall["killed"] / overall["applicable"], 6)
    killed_ids = {m["id"] for m in doc["mutants"] if m["killed"]}
    from_rules = {mid for ids in doc["rules"].values() for mid in ids["killed"]}
    assert killed_ids == from_rules
    declared = {
        "orders.order_id.unique",
        "orders.order_id.nullable",
        "orders.order_id.dtype",
        "orders.status.allowed_values",
        "orders.amount.min",
        "orders.amount.nullable",
    }
    assert declared <= set(doc["rules"])
    assert set(doc["rules_killed_none"]) == {r for r in declared if not doc["rules"][r]["killed"]}


def test_every_default_kind_is_enumerated_for_a_suitable_table() -> None:
    kinds = {m["kind"] for m in mutation_test({"orders": orders()}, STRICT).to_dict()["mutants"]}
    assert kinds == {
        "duplicates",
        "orphan_keys",
        "date_shift",
        "negative_amounts",
        "case_whitespace",
        "pii_fill",
        "type_change",
        "null_creep",
    }


def test_the_same_seed_gives_the_same_report() -> None:
    a = mutation_test({"orders": orders()}, STRICT, seed=9).to_dict()
    b = mutation_test({"orders": orders()}, STRICT, seed=9).to_dict()
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_null_creep_is_killed_by_a_null_rule() -> None:
    contract = {"columns": {"status": {"nullable": False}, "amount": {"dtype": "float"}}}
    m = by_id(mutation_test({"orders": orders()}, contract, seed=1, rate=0.05))
    assert m["null_creep.orders.status"]["killed_by"] == ["orders.status.nullable"]
    assert m["null_creep.orders.amount"]["killed_by"] == []  # the amount may be null: no rule


def test_a_dtype_rule_is_dropped_into_the_report_and_a_type_change_to_text_is_a_mutant() -> None:
    contract = {"columns": {"amount": {"dtype": "float"}}}
    doc = mutation_test({"orders": orders()}, contract, seed=1).to_dict()
    assert "orders.amount.dtype" in doc["rules"]
    mutant = by_id(mutation_test({"orders": orders()}, contract, seed=1))[
        "type_change.orders.amount"
    ]
    assert mutant["cells_changed"] == 400 and mutant["column"] == "amount"


def test_diff_adds_drift_kinds_to_killed_by() -> None:
    plain = by_id(mutation_test({"orders": orders()}, {}, seed=1, rate=0.3))
    assert plain["null_creep.orders.status"]["killed"] is False
    drifted = by_id(mutation_test({"orders": orders()}, {}, seed=1, rate=0.3, diff=True))
    assert any(k.startswith("drift:") for k in drifted["null_creep.orders.status"]["killed_by"])
    assert drifted["null_creep.orders.status"]["killed"] is True


def test_a_drift_section_in_the_contract_runs_the_comparison_without_the_flag() -> None:
    contract = {"drift": {"thresholds": {"null_rate": 0.01}}}
    m = by_id(mutation_test({"orders": orders()}, contract, seed=1, rate=0.3))
    assert any(k.startswith("drift:") for k in m["null_creep.orders.status"]["killed_by"])


def test_the_drift_policy_ignore_list_is_honoured() -> None:
    contract = {"drift": {"ignore": ["status"]}}
    m = by_id(mutation_test({"orders": orders()}, contract, seed=1, rate=0.3))
    assert m["null_creep.orders.status"]["killed"] is False


def test_a_plan_selects_the_mutants() -> None:
    plan = {
        "format": "shape-mutation-plan",
        "version": 1,
        "corruptions": [
            {"kind": "null_creep", "table": "orders", "column": "status", "rate": 0.2},
            {"kind": "duplicates", "table": "orders"},
        ],
    }
    doc = mutation_test({"orders": orders()}, STRICT, plan=plan, rate=0.05).to_dict()
    assert [m["id"] for m in doc["mutants"]] == ["null_creep.orders.status", "duplicates.orders"]
    assert doc["mutants"][0]["rate"] == 0.2 and doc["mutants"][1]["rate"] == 0.05


@pytest.mark.parametrize(
    "corruption, message",
    [
        ({"kind": "shred", "table": "orders", "column": "status"}, "unknown corruption"),
        ({"kind": "null_creep", "table": "orders", "column": "nope"}, "no column"),
        ({"kind": "null_creep", "table": "ghost", "column": "status"}, "table 'ghost'"),
        ({"kind": "null_creep", "table": "orders", "column": "status", "rate": 1.5}, "rate"),
        ({"kind": "null_creep", "table": "orders", "column": "status", "bogus": 1}, "unknown keys"),
    ],
)
def test_a_bad_plan_is_an_error(corruption: dict[str, Any], message: str) -> None:
    plan = {"format": "shape-mutation-plan", "version": 1, "corruptions": [corruption]}
    with pytest.raises(MutationError, match=message):
        mutation_test({"orders": orders()}, STRICT, plan=plan)


@pytest.mark.parametrize(
    "plan, message",
    [
        ({"version": 1, "corruptions": [{"kind": "duplicates"}]}, "format"),
        (
            {
                "format": "shape-mutation-plan",
                "version": 2,
                "corruptions": [{"kind": "duplicates"}],
            },
            "version 2",
        ),
        ({"format": "shape-mutation-plan", "corruptions": [{"kind": "duplicates"}]}, "version"),
        ({"format": "shape-mutation-plan", "version": 1, "corruptions": []}, "corruptions"),
    ],
)
def test_a_plan_declares_its_format_and_version(plan: dict[str, Any], message: str) -> None:
    with pytest.raises(MutationError, match=message):
        mutation_test({"orders": orders()}, STRICT, plan=plan)


def test_rate_boundaries() -> None:
    zero = mutation_test({"orders": orders()}, STRICT, rate=0.0).to_dict()
    # a type change turns the whole column to text whatever the rate; every other kind needs rows
    assert {m["kind"] for m in zero["mutants"] if m["status"] != "not_applicable"} == {
        "type_change"
    }
    full = mutation_test({"orders": orders()}, STRICT, rate=1.0).to_dict()
    assert full["score"]["overall"]["applicable"] > 0
    for bad in (-0.1, 1.1):
        with pytest.raises(MutationError, match="rate"):
            mutation_test({"orders": orders()}, STRICT, rate=bad)


def test_unusable_contracts_and_data() -> None:
    with pytest.raises(MutationError, match="unknown contract keys"):
        mutation_test({"orders": orders()}, {"nope": 1})
    with pytest.raises(MutationError, match="not valid JSON"):
        mutation_test({"orders": orders()}, __file__)
    with pytest.raises(MutationError, match="not found"):
        mutation_test("does/not/exist", STRICT)


def test_several_tables_need_a_tables_contract_and_report_per_table() -> None:
    items = pa.table(
        {
            "item_id": list(range(1, 201)),
            "order_id": [1 + i % 50 for i in range(200)],
            "qty": [1 + i % 5 for i in range(200)],
        }
    )
    contract = {"tables": {"orders": STRICT, "items": {"columns": {"item_id": {"unique": True}}}}}
    doc = mutation_test({"orders": orders(), "items": items}, contract, seed=3).to_dict()
    assert {"orders", "items"} == set(doc["score"]["by_table"])
    assert any(r.startswith("items.item_id") for r in doc["rules"])
    with pytest.raises(MutationError, match="tables"):
        mutation_test({"orders": orders(), "items": items}, STRICT)


def test_data_can_be_a_file(tmp_path: Path) -> None:
    import pyarrow.csv as pacsv

    pacsv.write_csv(orders(), tmp_path / "orders.csv")
    by = by_id(mutation_test(tmp_path / "orders.csv", STRICT, seed=1))
    assert by["duplicates.orders"]["killed"] is True


def test_a_rule_the_clean_data_already_fails_cannot_kill_a_mutant() -> None:
    contract = {"columns": {"status": {"allowed_values": ["new"]}, "order_id": {"unique": True}}}
    doc = mutation_test({"orders": orders()}, contract, seed=1).to_dict()
    assert doc["baseline_failed_rules"] == ["orders.status.allowed_values"]
    assert all("orders.status.allowed_values" not in m["killed_by"] for m in doc["mutants"])
    assert "orders.status.allowed_values" not in doc["rules_killed_none"]
    assert doc["rules"]["orders.status.allowed_values"]["killed"] == []
    by = {m["id"]: m for m in doc["mutants"]}
    assert by["duplicates.orders"]["killed_by"] == ["orders.order_id.unique"]
    assert by["null_creep.orders.amount"]["killed"] is False


def test_a_clean_contract_reports_no_baseline_failures() -> None:
    assert mutation_test({"orders": orders()}, STRICT).to_dict()["baseline_failed_rules"] == []
