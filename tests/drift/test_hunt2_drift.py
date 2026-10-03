"""HUNT2-profile: regression tests for the drift engine (#616, #617, #618, #619, #620, #621)."""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import pytest

import shape
from shape.drift import gate
from shape.profile import merge_profiles


def _p(df: pd.DataFrame, name: str | None = None, **kw):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return shape.profile(df, name=name, **kw) if name else shape.profile(df, **kw)


def _kinds(a, b, **kw) -> list[tuple]:
    return [(c["column"], c["kind"]) for c in shape.diff(a, b, **kw).changes]


# ----------------------------------------------------------------- #616 constant floats


@pytest.mark.parametrize("value", [0.1, 0.7, 1.1, 123456.789, -2.5e-7])
@pytest.mark.parametrize(("n1", "n2"), [(50, 60), (50, 70), (40, 75), (37, 53), (100, 150)])
def test_616_a_constant_float_column_does_not_drift(value: float, n1: int, n2: int) -> None:
    a = _p(pd.DataFrame({"price": [value] * n1}))
    b = _p(pd.DataFrame({"price": [value] * n2}))
    assert [k for k in _kinds(a, b) if k[0] == "price"] == []


def test_616_a_constant_that_moves_still_shifts() -> None:
    a = _p(pd.DataFrame({"price": [1.1] * 50}))
    b = _p(pd.DataFrame({"price": [1.2] * 60}))
    assert ("price", "mean_shift") in _kinds(a, b)


def test_616_a_constant_that_starts_varying_still_changes_spread() -> None:
    a = _p(pd.DataFrame({"price": [1.1] * 200}))
    rng = np.random.default_rng(0)
    b = _p(pd.DataFrame({"price": 1.1 + rng.normal(0, 0.01, 200)}))
    assert ("price", "spread_change") in _kinds(a, b)


# -------------------------------------------------------------------- #617 drop to zero


def test_617_an_empty_table_scores_one_and_fails_the_gate() -> None:
    rng = np.random.default_rng(0)
    base = _p(pd.DataFrame({"a": rng.normal(0, 1, 200)}))
    empty = _p(pd.DataFrame({"a": pd.Series([], dtype=float)}))
    scores = {c["kind"]: c["score"] for c in shape.diff(base, empty).changes}
    assert scores["row_count_change"] == 1.0
    assert scores["cardinality_change"] == 1.0
    assert gate(base, empty).to_dict()["passed"] is False


def test_617_a_spread_that_collapses_scores_one() -> None:
    rng = np.random.default_rng(0)
    base = _p(pd.DataFrame({"a": rng.normal(100, 10, 200)}))
    flat = _p(pd.DataFrame({"a": [100.0] * 100 + [101.0] * 100}))
    zero = _p(pd.DataFrame({"a": [100.0] * 200}))
    spread = {c["kind"]: c["score"] for c in shape.diff(base, zero).changes}
    assert spread["spread_change"] == 1.0
    # a partial drop keeps the documented 1 - min(r, 1/r)
    partial = {c["kind"]: c for c in shape.diff(base, flat).changes}["spread_change"]
    ratio = partial["current"] / partial["baseline"]
    assert partial["score"] == pytest.approx(1 - min(ratio, 1 / ratio), abs=1e-4)


# ------------------------------------------------------------------ #618 merged profiles


def test_618_a_merged_profile_has_no_false_pattern_change() -> None:
    def day(n: int, seed: int) -> pd.DataFrame:
        r = np.random.default_rng(seed)
        return pd.DataFrame(
            {
                "email": [f"user{r.integers(1e9)}@example.com" for _ in range(n)],
                "status": r.choice(["new", "paid", "shipped"], n),
                "amount": r.normal(100, 10, n),
            }
        )

    d1, d2 = day(500, 1), day(500, 2)
    merged = merge_profiles([_p(d1, sketches=True), _p(d2, sketches=True)])
    full = _p(pd.concat([d1, d2], ignore_index=True))
    assert _kinds(full, merged) == []
    assert _kinds(merged, full) == []


def test_618_a_real_pattern_change_is_still_reported() -> None:
    emails = pd.DataFrame({"c": [f"u{i}@example.com" for i in range(300)]})
    words = pd.DataFrame({"c": [f"word {i}" for i in range(300)]})
    assert ("c", "pattern_change") in _kinds(_p(emails), _p(words))


# ------------------------------------------------------------ #619 joint per-column policy


def _zips() -> tuple:
    rng = np.random.default_rng(0)
    n = 2000
    a = pd.DataFrame(
        {"zip": [f"{z:05d}" for z in rng.integers(10000, 99999, n)], "v": rng.normal(0, 1, n)}
    )
    b = a.copy()
    b.loc[: n // 5, "zip"] = "00000"
    return _p(a), _p(b)


def test_619_joint_kinds_follow_per_column_thresholds() -> None:
    pa_, pb_ = _zips()
    assert ("zip", "placeholder_surge") in _kinds(pa_, pb_)
    high = _kinds(pa_, pb_, column_thresholds={"zip": {"min_severity": "high"}})
    assert ("zip", "placeholder_surge") not in high
    share = _kinds(pa_, pb_, column_thresholds={"zip": {"placeholder_share": 0.5}})
    assert ("zip", "placeholder_surge") not in share
    # another column's threshold does not touch zip
    other = _kinds(pa_, pb_, column_thresholds={"v": {"placeholder_share": 0.5}})
    assert ("zip", "placeholder_surge") in other


def test_619_implausible_rows_follow_ignore_and_only() -> None:
    pa_, pb_ = _zips()
    assert ("(rows)", "implausible_rate_change") in _kinds(pa_, pb_)
    assert _kinds(pa_, pb_, only_columns=["v"]) == []
    assert _kinds(pa_, pb_, ignore_columns=["zip"]) == []
    assert ("(rows)", "implausible_rate_change") in _kinds(pa_, pb_, only_columns=["zip"])


# ------------------------------------------------------- #620 scope of patterns, #621 table


def _orders(n: int, status: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"amount": range(n), "status": status})


def test_620_only_star_keeps_table_level_changes() -> None:
    a = _p(_orders(1000, ["a", "b"] * 500), name="orders")
    b = _p(_orders(100, ["a", "b"] * 50), name="orders")
    assert (None, "row_count_change") in _kinds(a, b)
    assert (None, "row_count_change") in _kinds(a, b, only_columns=["*"])
    assert (None, "row_count_change") in _kinds(a, b, only_columns=["orders.*"])
    # a column list without the table keeps only those columns
    assert (None, "row_count_change") not in _kinds(a, b, only_columns=["amount"])


def test_620_table_column_patterns_match_a_single_table() -> None:
    a = _p(_orders(1000, ["a", "b"] * 500), name="orders")
    b = _p(_orders(1000, ["a"] * 900 + ["c"] * 100), name="orders")
    base = _kinds(a, b)
    assert ("status", "category_shift") in base
    assert ("status", "category_shift") not in _kinds(a, b, ignore_columns=["orders.status"])
    assert _kinds(a, b, only_columns=["orders.status"]) == [k for k in base if k[0] == "status"]
    lax = _kinds(a, b, column_thresholds={"orders.status": {"min_severity": "high"}})
    assert [k for k in lax if k[0] == "status"] == []
    # another table's name does not match
    assert ("status", "category_shift") in _kinds(a, b, ignore_columns=["other.status"])


def _dataset(rows: int) -> dict[str, pd.DataFrame]:
    return {
        "orders": _orders(rows, (["a", "b"] * rows)[:rows]),
        "customers": pd.DataFrame({"id": range(50), "city": ["x", "y"] * 25}),
    }


def test_620_star_threshold_treats_single_tables_and_datasets_alike() -> None:
    single_a = _p(_orders(1000, ["a", "b"] * 500), name="orders")
    single_b = _p(_orders(100, ["a", "b"] * 50), name="orders")
    data_a, data_b = _p(_dataset(1000)), _p(_dataset(100))
    th = {"*": {"min_severity": "high"}}
    single = [
        k for k in _kinds(single_a, single_b, column_thresholds=th) if k[1] == "row_count_change"
    ]
    data = [k for k in _kinds(data_a, data_b, column_thresholds=th) if k[1] == "row_count_change"]
    assert single == data == []


def test_621_a_dataset_row_count_change_names_its_table() -> None:
    changes = shape.diff(_p(_dataset(1000)), _p(_dataset(100))).changes
    rows = [c for c in changes if c["kind"] == "row_count_change"]
    assert len(rows) == 1
    assert rows[0]["table"] == "orders" and rows[0]["column"] is None
    # a single table's record is unchanged (no table key)
    single = shape.diff(
        _p(_orders(1000, ["a", "b"] * 500), name="orders"),
        _p(_orders(100, ["a", "b"] * 50), name="orders"),
    ).changes
    assert "table" not in next(c for c in single if c["kind"] == "row_count_change")


def test_621_table_added_and_removed_name_their_table() -> None:
    both = _dataset(1000)
    extra = {**both, "returns": pd.DataFrame({"id": range(10)})}
    added = [c for c in shape.diff(_p(both), _p(extra)).changes if c["kind"] == "table_added"]
    removed = [c for c in shape.diff(_p(extra), _p(both)).changes if c["kind"] == "table_removed"]
    assert [c["table"] for c in added] == [c["table"] for c in removed] == ["returns"]
