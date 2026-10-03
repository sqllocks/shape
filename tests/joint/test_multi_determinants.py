"""W3-08 (#232): two-column determinants in ``joint.dependencies``, ``shape diff`` and the ``fd``
contract rule."""

from __future__ import annotations

from typing import Any

import numpy as np
import pyarrow as pa
import pytest

import shape
from shape.contracts.v1 import ContractError
from shape.profile.dependencies import functional_dependency
from shape.profile.joint import analyze


def _register_table(
    n: int = 6000, seed: int = 5, violate: float = 0.0, stores: int = 12
) -> pa.Table:
    """``(store_id, register_no) -> cashier`` with confidence 1: each (store, register) has one
    cashier, and neither column alone fixes the cashier."""
    rng = np.random.default_rng(seed)
    store = rng.integers(0, stores, n)
    reg = rng.integers(0, 5, n)
    cashier = np.array([f"c{s * 5 + r}" for s, r in zip(store, reg, strict=True)])
    if violate:
        bad = rng.random(n) < violate
        cashier[bad] = [f"c{x}" for x in rng.integers(0, stores * 5, int(bad.sum()))]
    return pa.table(
        {
            "store_id": pa.array([f"s{x}" for x in store]),
            "register_no": pa.array([f"r{x}" for x in reg]),
            "cashier": pa.array(cashier),
            "amount": pa.array(rng.gamma(3.0, 30.0, n)),
        }
    )


def _deps(table: pa.Table) -> list[dict[str, Any]]:
    return list(shape.profile(table).to_dict()["joint"]["dependencies"])


def _pairs(deps: list[dict[str, Any]]) -> dict[tuple[tuple[str, ...], str], dict[str, Any]]:
    return {(tuple(d["determinant"]), d["dependent"]): d for d in deps if len(d["determinant"]) > 1}


def test_a_planted_pair_dependency_is_found_and_neither_column_alone_is_listed() -> None:
    deps = _deps(_register_table())
    found = _pairs(deps)[(("register_no", "store_id"), "cashier")]
    assert found["confidence"] == 1.0
    assert found["violating_groups"] == 0 and found["violations"] == []
    assert set(found) >= {
        "determinant",
        "dependent",
        "confidence",
        "baseline",
        "support",
        "groups",
        "violating_groups",
        "violations",
    }
    assert found["groups"] == 60
    single = {(tuple(d["determinant"]), d["dependent"]) for d in deps if len(d["determinant"]) == 1}
    assert (("store_id",), "cashier") not in single
    assert (("register_no",), "cashier") not in single


def test_the_numbers_equal_the_fd_command_on_the_same_rows() -> None:
    table = _register_table(violate=0.03, seed=6)
    found = _pairs(_deps(table))[(("register_no", "store_id"), "cashier")]
    rows = table.to_pylist()
    ref = functional_dependency(rows, ("register_no", "store_id"), "cashier")
    assert found["confidence"] == pytest.approx(ref.confidence, abs=1e-6)
    assert found["violating_groups"] == ref.violating_groups
    assert found["groups"] == ref.determinant_groups
    # the determinant's order does not matter to the command
    flipped = functional_dependency(rows, ("store_id", "register_no"), "cashier")
    assert found["confidence"] == pytest.approx(flipped.confidence, abs=1e-6)


def test_a_planted_violation_lowers_the_confidence_and_lists_the_worst_groups() -> None:
    found = _pairs(_deps(_register_table(violate=0.1)))[(("register_no", "store_id"), "cashier")]
    assert 0.85 < found["confidence"] < 0.95
    assert found["violating_groups"] > 0
    worst = found["violations"][0]
    assert len(worst["determinant_value"]) == 2
    assert worst["distinct_dependents"] >= 2 and worst["rows"] > 0
    assert sum(worst["dependent_values"].values()) <= worst["rows"]


def test_a_pair_below_the_minimum_confidence_is_not_listed() -> None:
    assert (("register_no", "store_id"), "cashier") not in _pairs(
        _deps(_register_table(violate=0.6))
    )


def test_a_column_that_alone_explains_the_dependent_makes_the_pair_moot() -> None:
    rng = np.random.default_rng(7)
    n = 4000
    region = rng.integers(0, 20, n)
    t = pa.table(
        {
            "city": pa.array([f"c{x}" for x in region]),
            "zone": pa.array([f"z{x}" for x in rng.integers(0, 4, n)]),
            "state": pa.array([f"s{x // 4}" for x in region]),  # city alone fixes state
        }
    )
    deps = _deps(t)
    assert not _pairs(deps)
    assert (("city",), "state") in {(tuple(d["determinant"]), d["dependent"]) for d in deps}


def test_the_margin_of_one_percent_over_the_better_single_column() -> None:
    """The pair is listed only when both columns alone fall short of it by more than 0.01."""
    rng = np.random.default_rng(8)
    n = 20000
    a = rng.integers(0, 6, n)
    b = rng.integers(0, 6, n)
    for noise, listed in ((0.0, True), (0.0005, True)):
        y = (a * 6 + b).astype(np.int64)
        flip = rng.random(n) < noise
        y[flip] = rng.integers(0, 36, int(flip.sum()))
        t = pa.table(
            {
                "a": pa.array([f"a{x}" for x in a]),
                "b": pa.array([f"b{x}" for x in b]),
                "y": pa.array([f"y{x}" for x in y]),
            }
        )
        assert bool(_pairs(_deps(t))) is listed
    # b alone already gets within 0.01 of the pair: y depends on a only 0.5% of the time
    y = np.where(rng.random(n) < 0.005, a, b).astype(np.int64)
    t = pa.table(
        {
            "a": pa.array([f"a{x}" for x in a]),
            "b": pa.array([f"b{x}" for x in b]),
            "y": pa.array([f"y{x}" for x in y]),
        }
    )
    assert not _pairs(_deps(t))


def test_a_pair_that_is_a_candidate_key_is_not_a_determinant() -> None:
    n = 400
    t = pa.table(
        {
            "region": pa.array([f"r{i % 20}" for i in range(n)]),
            "store": pa.array([f"s{i // 20}" for i in range(n)]),
            "flag": pa.array([f"f{i % 3}" for i in range(n)]),
        }
    )
    j = shape.profile(t).to_dict()["joint"]
    assert {"fields": ["region", "store"], "rows": n, "distinct": n, "exact": True} in j["keys"]
    assert not any(d["determinant"] == ["region", "store"] for d in j["dependencies"]), (
        "a key determines every column trivially"
    )


def test_pairs_are_over_categorical_columns_and_a_numeric_column_is_not_a_determinant() -> None:
    rng = np.random.default_rng(9)
    n = 3000
    x = rng.standard_normal(n)  # continuous: no categorical view
    g = rng.integers(0, 4, n)
    t = pa.table(
        {"x": pa.array(x), "g": pa.array([f"g{i}" for i in g]), "h": pa.array([f"h{i}" for i in g])}
    )
    assert not any("x" in d["determinant"] or d["dependent"] == "x" for d in _deps(t))


def test_the_number_of_pairs_tried_is_capped_by_the_budget() -> None:
    rng = np.random.default_rng(10)
    n = 3000
    cols = {f"c{i:02d}": pa.array([f"v{x}" for x in rng.integers(0, 8, n)]) for i in range(16)}
    table = pa.table(cols)
    from shape.profile.reference.readers import _arrow_cols

    j = analyze.analyze_table(_arrow_cols(table), n)
    assert j is not None
    assert j["multi_determinant_pairs_evaluated"] <= analyze.SMALL.max_fd_multi
    assert j["multi_determinant_capped"] is True  # 120 pairs x 14 dependents: more than the budget
    assert analyze.LARGE.max_fd_multi < analyze.SMALL.max_fd_multi
    big = pa.table({k: pa.array(np.tile(np.asarray(v), 8)) for k, v in cols.items()})
    jl = analyze.analyze_table(_arrow_cols(big), big.num_rows)
    assert jl is not None and jl["sampled"] is True
    assert jl["multi_determinant_pairs_evaluated"] <= analyze.LARGE.max_fd_multi
    assert (
        len([d for d in jl["dependencies"] if len(d["determinant"]) > 1])
        <= analyze.MAX_PAIR_DEPENDENCIES
    )


def test_single_column_dependencies_are_unchanged_by_the_pairs() -> None:
    deps = _deps(_register_table())
    singles = [d for d in deps if len(d["determinant"]) == 1]
    assert deps[: len(singles)] == singles  # the pairs follow the single-column entries
    assert all(d["determinant"] == sorted(d["determinant"]) for d in deps)


# --- diff -----------------------------------------------------------------------------------------


def test_a_planted_violation_makes_diff_report_dependency_broken_for_the_pair() -> None:
    good = shape.profile(_register_table(seed=1))
    bad = shape.profile(_register_table(seed=2, violate=0.12))
    result = shape.diff(good, bad)
    broken = {c["column"]: c for c in result.changes if c["kind"] == "dependency_broken"}
    change = broken["register_no, store_id -> cashier"]
    assert change["severity"] == "high"
    assert change["baseline"] == 1.0 and 0.8 < change["current"] < 0.95
    assert change["detail"]["determinant"] == ["register_no", "store_id"]
    assert "register_no" in change["message"] and "store_id" in change["message"]
    assert "(register_no, store_id)" in change["message"]


def test_two_samples_of_one_table_report_nothing() -> None:
    a = shape.profile(_register_table(seed=1))
    b = shape.profile(_register_table(seed=2))
    assert not [c for c in shape.diff(a, b).changes if c["kind"] == "dependency_broken"]


def test_a_stronger_single_column_is_not_a_broken_pair() -> None:
    """If one column comes to explain the dependent alone, the pair leaves the list without a
    break."""
    good = shape.profile(_register_table(seed=1))
    rng = np.random.default_rng(3)
    n = 6000
    store = rng.integers(0, 12, n)
    t = pa.table(
        {
            "store_id": pa.array([f"s{x}" for x in store]),
            "register_no": pa.array([f"r{x}" for x in rng.integers(0, 5, n)]),
            "cashier": pa.array([f"c{x}" for x in store]),  # the store alone fixes it
            "amount": pa.array(rng.gamma(3.0, 30.0, n)),
        }
    )
    changes = shape.diff(good, shape.profile(t)).changes
    assert not [c for c in changes if c["column"] == "register_no, store_id -> cashier"]


def test_a_pair_outside_the_analysed_columns_is_not_reported() -> None:
    good = shape.profile(_register_table(seed=1))
    t = _register_table(seed=2, violate=0.3).drop_columns(["register_no"])
    changes = shape.diff(good, shape.profile(t)).changes
    assert not [
        c for c in changes if c["kind"] == "dependency_broken" and "register_no" in c["column"]
    ]


def test_a_baseline_without_pairs_reports_no_pair_changes() -> None:
    old = shape.load("tests/fixtures/profiles/pre_w3_07.shape")
    new = shape.profile(_register_table())
    assert not [c for c in shape.diff(old, new).changes if c["kind"] == "dependency_broken"]


# --- the fd contract rule -------------------------------------------------------------------


def _check(table: pa.Table, rule: dict[str, Any]) -> Any:
    return shape.check(shape.profile(table), {"fd": [rule]})


def test_the_fd_rule_with_a_list_determinant_reads_the_pair() -> None:
    ok = _check(
        _register_table(),
        {
            "determinant": ["store_id", "register_no"],
            "dependent": "cashier",
            "min_confidence": 0.99,
        },
    )
    assert ok.passed
    flipped = _check(
        _register_table(),
        {
            "determinant": ["register_no", "store_id"],
            "dependent": "cashier",
            "min_confidence": 0.99,
        },
    )
    assert flipped.passed
    bad = _check(
        _register_table(violate=0.1),
        {
            "determinant": ["store_id", "register_no"],
            "dependent": "cashier",
            "min_confidence": 0.99,
        },
    )
    assert not bad.passed
    v = bad.violations[0]
    assert v["rule"] == "fd" and 0.85 < v["observed"]["confidence"] < 0.95
    assert v["observed"]["violating_groups"] > 0


def test_the_fd_rule_on_a_pair_outside_the_analysed_columns_is_not_measured() -> None:
    table = _register_table()
    rng = np.random.default_rng(1)
    price = pa.array(rng.choice(np.linspace(1.0, 2.0, 200), table.num_rows))  # 200 floats: numeric
    table = table.append_column("price", price)
    result = _check(
        table,
        {"determinant": ["store_id", "price"], "dependent": "cashier", "min_confidence": 0.9},
    )
    assert not result.passed
    assert "not measured" in str(result.violations[0]["observed"])


def test_the_fd_rule_on_a_pair_that_is_not_listed_is_not_measured_below_the_listing_floor() -> None:
    low = _check(
        _register_table(violate=0.6),
        {"determinant": ["store_id", "register_no"], "dependent": "cashier", "min_confidence": 0.5},
    )
    assert "not measured" in str(low.violations[0]["observed"])
    high = _check(
        _register_table(violate=0.6),
        {
            "determinant": ["store_id", "register_no"],
            "dependent": "cashier",
            "min_confidence": 0.95,
        },
    )
    assert high.violations[0]["observed"]["confidence_below"] == 0.8


def test_the_fd_rule_with_a_unique_column_or_a_key_pair_passes() -> None:
    n = 400
    t = pa.table(
        {
            "region": pa.array([f"r{i % 20}" for i in range(n)]),
            "store": pa.array([f"s{i // 20}" for i in range(n)]),
            "tag": pa.array([f"t{i % 7}" for i in range(n)]),
        }
    )
    rule = {"determinant": ["region", "store"], "dependent": "tag", "min_confidence": 1.0}
    assert _check(t, rule).passed  # unique together: determines every column


def test_a_profile_without_the_joint_entry_says_not_measured_for_a_pair() -> None:
    result = shape.check(
        shape.profile(_register_table(), joint=False),
        {
            "fd": [
                {
                    "determinant": ["store_id", "register_no"],
                    "dependent": "cashier",
                    "min_confidence": 0.9,
                }
            ],
        },
    )
    assert not result.passed and "not measured" in str(result.violations[0]["observed"])


def test_a_malformed_fd_rule_is_still_rejected() -> None:
    with pytest.raises(ContractError):
        _check(
            _register_table(), {"determinant": [], "dependent": "cashier", "min_confidence": 0.9}
        )


def test_a_current_profile_made_before_pairs_cannot_break_one() -> None:
    """``pre_w3_08.shape`` analysed the same columns without trying pairs: not a break."""
    rng = np.random.default_rng(8)
    n = 1500
    region = rng.choice(["north", "south", "east", "west"], n, p=[0.4, 0.3, 0.2, 0.1])
    tier = np.where(rng.random(n) < 0.8, "gold", rng.choice(["gold", "silver", "bronze"], n))
    table = pa.table(
        {
            "region": region,
            "tier": tier,
            "store": [f"s{i % 9}" for i in range(n)],
            "reg_no": [f"r{(i // 9) % 4}" for i in range(n)],
            "cashier": [f"c{(i % 9) * 4 + (i // 9) % 4}" for i in range(n)],
        }
    )
    new = shape.profile(table)
    assert any(len(d["determinant"]) == 2 for d in new.to_dict()["joint"]["dependencies"])
    old = shape.load("tests/fixtures/profiles/pre_w3_08.shape")
    changes = shape.diff(new, old).changes
    assert not [c for c in changes if c["kind"] == "dependency_broken" and ", " in c["column"]]
