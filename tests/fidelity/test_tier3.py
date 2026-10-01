"""Tier 3: dependency tree, drift, PSI and bootstrap."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest
from fid_helpers import make_table

from shape.fidelity.tier3 import (
    ChowLiuTree,
    DriftMonitor,
    _cut,
    _mutual_information,
    bootstrap_table,
    compare_trees,
    population_stability_index,
    psi_report,
)


def _dependent(seed=0, n=1500, noise=0.2):
    rng = np.random.default_rng(seed)
    x = rng.normal(size=n)
    return pa.table(
        {
            "x": x,
            "y": x * 2 + rng.normal(0, noise, n),  # depends on x
            "z": rng.normal(size=n),  # independent
            "cat": np.where(x > 0, "pos", "neg"),  # depends on x
        }
    )


def test_cut_equals_pandas_cut():
    rng = np.random.default_rng(0)
    for x in (rng.normal(size=500), rng.integers(0, 4, 300).astype(float), np.full(20, 7.0),
              np.zeros(5), np.array([-3.0, 3.0]), rng.exponential(size=2000)):
        want = pd.cut(pd.Series(x), bins=10, labels=False).to_numpy()
        assert _cut(x, 10).tolist() == want.tolist()


def test_mutual_information_equals_the_crosstab_formula():
    rng = np.random.default_rng(1)
    x, y = rng.integers(0, 5, 400), rng.integers(0, 3, 400)
    xy = pd.crosstab(pd.Series(x), pd.Series(y), normalize=True)
    px, py = xy.sum(axis=1).values, xy.sum(axis=0).values
    want = 0.0
    for i, xi in enumerate(xy.index):
        for j, yj in enumerate(xy.columns):
            p = xy.loc[xi, yj]
            if p > 0:
                want += p * np.log(p / (px[i] * py[j] + 1e-12) + 1e-12)
    assert _mutual_information(x, y) == pytest.approx(max(0.0, want), rel=1e-12)
    assert _mutual_information(np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)) == 0.0


def test_tree_links_the_dependent_columns():
    res = ChowLiuTree().fit(_dependent())
    assert len(res.edges) == 3 and res.column_order == ["x", "y", "z", "cat"]
    pairs = {frozenset((e.parent, e.child)) for e in res.edges}
    assert frozenset(("x", "y")) in pairs
    assert res.edges[0].mutual_information >= res.edges[1].mutual_information >= 0
    assert res.mutual_info_matrix["x"]["y"] == res.mutual_info_matrix["y"]["x"]
    assert res.mutual_info_matrix["x"]["y"] > res.mutual_info_matrix["x"]["z"]
    json.dumps(res.to_dict(), allow_nan=False)


def test_tree_uses_the_first_rows_and_handles_missing_values_and_timestamps():
    t = make_table(1, n=300)
    res = ChowLiuTree(sample_size=50).fit(t)
    assert len(res.edges) == t.num_columns - 1
    assert ChowLiuTree().fit(pa.table({"a": [1, 2, 3]})).edges == []


def test_comparing_trees():
    a = ChowLiuTree().fit(_dependent(0))
    same = compare_trees(a, ChowLiuTree().fit(_dependent(1)))
    assert same["edge_overlap"] >= 0.5 and same["columns_compared"] == 4
    broken = _dependent(2, noise=0.2).set_column(1, "y", pa.array(np.random.default_rng(9).normal(size=1500)))
    worse = compare_trees(a, ChowLiuTree().fit(broken))
    assert worse["mutual_information_max_abs_diff"] > same["mutual_information_max_abs_diff"]
    assert compare_trees(a, a) == {
        "edge_overlap": 1.0,
        "mutual_information_mean_abs_diff": 0.0,
        "mutual_information_max_abs_diff": 0.0,
        "columns_compared": 4,
    }


def test_psi_of_identical_samples_is_zero_and_grows_with_the_shift():
    x = np.random.default_rng(0).normal(size=2000)
    assert population_stability_index(x, x) == pytest.approx(0.0, abs=1e-12)
    assert population_stability_index(x, x + 3) > 1.0
    assert population_stability_index(np.ones(5), np.ones(8)) == 0.0


def test_psi_formula():
    e, a = np.array([1.0, 1, 1, 2, 3, 4]), np.array([1.0, 2, 3, 4, 4, 4, 4])
    bins = np.linspace(1, 4, 11)
    ep = np.histogram(e, bins)[0] / 6 + 1e-10
    ap = np.histogram(a, bins)[0] / 7 + 1e-10
    assert population_stability_index(e, a) == pytest.approx(np.sum((ap - ep) * np.log(ap / ep)))


def test_psi_report_needs_no_scipy_and_flags_the_shifted_columns(no_scipy):
    real, same, shifted = make_table(1), make_table(2), make_table(3, shift=60.0)
    ok = psi_report(real, same)
    assert ok.drifted_columns == [] and all(r.method == "psi" for r in ok.columns.values())
    assert set(ok.skipped) == {"email", "day"} and "at" in ok.columns
    bad = psi_report(real, shifted)
    assert "amount" in bad.drifted_columns and 0 < bad.drift_fraction <= 1
    assert "bimodal" in bad.drifted_columns
    json.dumps(bad.to_dict(), allow_nan=False)
    assert psi_report(real, shifted, threshold=1e9).drifted_columns == []


def test_psi_report_scores_text_by_category_share_and_leaves_out_internal_columns():
    a = pa.table({"c": ["x"] * 90 + ["y"] * 10, "_shape_f": [1] * 100})
    b = pa.table({"c": ["x"] * 10 + ["y"] * 90, "_shape_f": [1] * 100})
    r = psi_report(a, b)
    assert list(r.columns) == ["c"] and r.columns["c"].is_drifted
    assert psi_report(a, a).columns["c"].test_statistic == pytest.approx(0.0, abs=1e-12)


def test_psi_report_type_mismatch_fails_closed():
    r = psi_report(pa.table({"c": list(range(20))}), pa.table({"c": ["a"] * 20}))
    assert r.columns["c"].method == "error" and r.columns["c"].is_drifted


def test_short_columns_are_not_tested():
    assert psi_report(pa.table({"c": [1, 2, 3]}), pa.table({"c": [1, 2, 3]})).columns == {}


class TestDriftMonitor:
    @pytest.fixture(autouse=True)
    def _scipy(self):
        pytest.importorskip("scipy")

    def test_detects_a_shift_in_numbers_and_in_categories(self):
        rng = np.random.default_rng(0)
        a = pa.table({"n": rng.normal(size=800), "c": rng.choice(["a", "b", "c"], 800)})
        b = pa.table(
            {"n": rng.normal(1.0, 1, 800), "c": rng.choice(["a", "b", "c"], 800, p=[0.7, 0.2, 0.1])}
        )
        rep = DriftMonitor().compare(a, b)
        assert rep.drifted_columns == ["n", "c"]
        assert rep.columns["n"].method == "ks" and rep.columns["c"].method == "chi2"
        assert rep.columns["n"].p_value < 1e-10 and rep.columns["c"].p_value < 0.05
        assert rep.drift_fraction == 1.0 and 0 < rep.overall_drift_score <= 1
        same = DriftMonitor().compare(a, a)
        assert same.drifted_columns == [] and same.columns["n"].test_statistic == 0.0

    def test_equals_scipy_and_the_documented_formulas(self):
        from scipy import stats

        rng = np.random.default_rng(3)
        x, y = rng.normal(size=600), rng.normal(0.2, 1.1, 700)
        rep = DriftMonitor().compare(pa.table({"n": x}), pa.table({"n": y}))
        r = rep.columns["n"]
        stat, p = stats.ks_2samp(x, y)
        psi = population_stability_index(x, y)
        assert r.test_statistic == pytest.approx(stat) and r.p_value == pytest.approx(p)
        assert r.drift_score == pytest.approx(min(1.0, stat + psi / 2))
        assert r.is_drifted == (p < 0.05 or psi > 0.2)

    def test_chi_squared_equals_scipy(self):
        from scipy import stats

        ref = ["a"] * 50 + ["b"] * 30 + ["c"] * 20
        cur = ["a"] * 20 + ["b"] * 40 + ["c"] * 30 + ["d"] * 10
        r = DriftMonitor().compare(pa.table({"k": ref}), pa.table({"k": cur})).columns["k"]
        ra, ca = np.array([50, 30, 20, 0.0]), np.array([20, 40, 30, 10.0])
        exp = ra * (ca.sum() / (ra.sum() + 1e-10))
        stat, p = stats.chisquare(ca + 1, exp + 1)
        assert r.test_statistic == pytest.approx(stat) and r.p_value == pytest.approx(p, rel=1e-9)
        assert r.drift_score == pytest.approx(min(1.0, stat / (4 * 10 + 1)))

    def test_samples_5000_rows_and_skips_small_and_internal_columns(self):
        rng = np.random.default_rng(0)
        big = pa.table({"n": rng.normal(size=20000), "_shape_i": rng.normal(size=20000)})
        rep = DriftMonitor().compare(big, big)
        assert list(rep.columns) == ["n"] and rep.drifted_columns == []
        assert DriftMonitor().compare(pa.table({"n": [1.0] * 5}), pa.table({"n": [1.0] * 5})).columns == {}

    def test_type_mismatch_and_single_category_cases(self):
        r = DriftMonitor().compare(pa.table({"c": list(range(20))}), pa.table({"c": ["a"] * 20}))
        assert r.columns["c"].method == "error" and r.columns["c"].is_drifted
        one = DriftMonitor().compare(pa.table({"c": ["a"] * 20}), pa.table({"c": ["a"] * 20}))
        assert one.columns["c"].method == "chi2" and not one.columns["c"].is_drifted


def test_ks_without_scipy_raises_with_the_extra_named(no_scipy):
    t = pa.table({"n": np.arange(30.0)})
    with pytest.raises(ImportError, match=r"sqllocks-shape\[advanced\]"):
        DriftMonitor().compare(t, t)


class TestBootstrap:
    def test_deterministic_resample_of_the_source_rows(self):
        src = pa.table({"k": range(100), "s": [f"v{i}" for i in range(100)]})
        a, res = bootstrap_table(src, 500, "t", seed=3, add_jitter=False)
        b, _ = bootstrap_table(src, 500, "t", seed=3, add_jitter=False)
        assert a.equals(b) and a.num_rows == 500
        assert set(a["k"].to_pylist()) <= set(range(100))
        assert [f"v{k}" for k in a["k"].to_pylist()] == a["s"].to_pylist()  # whole rows
        assert not a.equals(bootstrap_table(src, 500, seed=4, add_jitter=False)[0])
        assert (res.table_name, res.n_rows, res.n_bootstrap_samples, res.seed) == ("t", 500, 500, 3)
        assert res.source_rows_used == 100

    def test_is_numpys_default_rng_stream(self):
        src = pa.table({"x": np.arange(50.0), "y": np.arange(50.0) ** 2})
        got, _ = bootstrap_table(src, 20, seed=11)
        rng = np.random.default_rng(11)
        idx = rng.integers(0, 50, size=20)
        for name in ("x", "y"):
            data = src[name].to_numpy()
            jit = rng.normal(0, np.std(data, ddof=1) * 0.01, size=20)
            assert got[name].to_numpy() == pytest.approx(data[idx] + jit, rel=1e-12)

    def test_jitter_only_on_numbers_with_spread_and_nulls_stay_null(self):
        src = pa.table(
            {
                "i": pa.array([1, 2, 3, None, 5]),
                "c": [7, 7, 7, 7, 7],
                "f": [1.5, 2.5, 3.5, 4.5, 5.5],
                "b": [True, False, True, False, True],
                "s": list("abcde"),
            }
        )
        out, _ = bootstrap_table(src, 40, seed=1)
        assert out["i"].type == pa.float64() and out["f"].type == pa.float64()
        assert out["c"].type == pa.int64() and out["c"].to_pylist() == [7] * 40  # no spread
        assert out["b"].type == pa.bool_() and out["s"].type == pa.string()
        assert out["i"].null_count == out["i"].to_pylist().count(None) > 0
        none, _ = bootstrap_table(src, 40, seed=1, add_jitter=False)
        assert none["f"].type == pa.float64() and set(none["f"].to_pylist()) <= set(src["f"].to_pylist())

    def test_default_size_zero_rows_and_empty_source(self):
        src = pa.table({"a": [1, 2, 3]})
        assert bootstrap_table(src)[0].num_rows == 3
        assert bootstrap_table(src, 0)[0].num_rows == 0
        with pytest.raises(ValueError, match="empty"):
            bootstrap_table(src.slice(0, 0))
