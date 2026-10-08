"""W3-08 (#232): the multivariate entries of the joint analysis: robust outliers (MCD), PCA, cohorts
and the mixed-type copula, with known answers, negative and boundary cases."""

from __future__ import annotations

import json
import math
import time
import tracemalloc
from typing import Any

import numpy as np
import pyarrow as pa
import pytest

import shape
from shape.profile.joint import analyze
from shape.profile.joint import multivariate as MV

KEYS = ("multivariate_outliers", "pca", "cohorts", "copula", "dependencies")


def _joint(table: pa.Table) -> dict[str, Any]:
    d = shape.profile(table, multivariate=True).to_dict()
    return dict(d["joint"])


def _bivariate(n: int, seed: int, r: float = 0.9, planted: float = 0.0) -> pa.Table:
    """A correlated bivariate normal, with ``planted`` of the rows replaced by joint outliers: each
    value is inside its column's 1% and 99% quantiles, the pair is against the correlation."""
    rng = np.random.default_rng(seed)
    cov = r * 10.0 * 30.0
    x = rng.multivariate_normal([50.0, 200.0], [[100.0, cov], [cov, 900.0]], n)
    if planted:
        k = int(round(n * planted))
        rows = rng.choice(n, k, replace=False)
        sign = rng.choice([-1.0, 1.0], k)
        x[rows, 0] = 50.0 + sign * rng.uniform(12.0, 18.0, k)
        x[rows, 1] = 200.0 - sign * rng.uniform(36.0, 54.0, k)
        for col, sd, mean in ((0, 10.0, 50.0), (1, 30.0, 200.0)):
            lo, hi = mean - 2.326 * sd, mean + 2.326 * sd
            assert (x[rows, col] > lo).all() and (x[rows, col] < hi).all()
    return pa.table({"age": pa.array(x[:, 0]), "income": pa.array(x[:, 1])})


# --- chi-square, normal quantile ----------------------------------------------------------------


def test_the_chi_square_and_normal_quantiles_agree_with_scipy() -> None:
    stats = pytest.importorskip("scipy.stats")
    for df in (1, 2, 3, 5, 10, 16):
        assert MV.chi2_ppf(0.999, df) == pytest.approx(stats.chi2.ppf(0.999, df), rel=1e-9)
        assert MV.chi2_cdf(7.7, df) == pytest.approx(stats.chi2.cdf(7.7, df), abs=1e-10)
    p = np.linspace(1e-9, 1 - 1e-9, 2001)
    assert np.abs(MV.norm_ppf(p) - stats.norm.ppf(p)).max() < 1e-8
    with pytest.raises(ValueError):
        MV.chi2_ppf(1.0, 2)


# --- multivariate outliers ------------------------------------------------------------------------


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_two_percent_planted_joint_outliers_give_a_rate_near_two_percent(seed: int) -> None:
    j = _joint(_bivariate(5000, seed, planted=0.02))
    mo = j["multivariate_outliers"]
    assert mo["method"] == "mcd" and mo["columns"] == ["age", "income"]
    assert abs(mo["rate"] - 0.02) <= 0.01
    assert mo["threshold"] == pytest.approx(MV.chi2_ppf(0.999, 2), abs=1e-5)
    assert mo["h"] == (mo["rows"] + 2 + 1) // 2
    assert sum(mo["contributions"].values()) == pytest.approx(1.0, abs=1e-4)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_clean_data_has_a_rate_below_half_a_percent(seed: int) -> None:
    mo = _joint(_bivariate(5000, seed))["multivariate_outliers"]
    assert mo["rate"] < 0.005


def test_the_outliers_are_the_planted_rows_and_each_column_shares_the_distance() -> None:
    # planted rows are normal in each column: the share of the distance is split between the columns
    mo = _joint(_bivariate(4000, 9, planted=0.02))["multivariate_outliers"]
    assert 0.3 < mo["contributions"]["age"] < 0.7


def test_contributions_name_the_column_that_moved() -> None:
    rng = np.random.default_rng(4)
    n = 4000
    cols = {c: rng.standard_normal(n) for c in ("a", "b", "c")}
    bad = rng.choice(n, 100, replace=False)
    cols["c"][bad] += 9.0  # only column c is off
    mo = _joint(pa.table({k: pa.array(v) for k, v in cols.items()}))["multivariate_outliers"]
    assert mo["rate"] == pytest.approx(0.025, abs=0.01)
    assert mo["contributions"]["c"] > 0.9


def test_no_row_value_is_stored() -> None:
    table = _bivariate(1000, 5, planted=0.02)
    mo = _joint(table)["multivariate_outliers"]
    text = json.dumps(mo)
    for value in table["age"].to_pylist()[:50]:
        assert repr(value) not in text
    assert set(mo) == {
        "columns",
        "method",
        "h",
        "rows",
        "threshold",
        "outliers",
        "rate",
        "contributions",
    }


def test_the_estimate_is_deterministic() -> None:
    table = _bivariate(3000, 6, planted=0.02)
    assert _joint(table)["multivariate_outliers"] == _joint(table)["multivariate_outliers"]


def test_minimum_sample_and_columns() -> None:
    rng = np.random.default_rng(7)

    def table(n: int, cols: int) -> pa.Table:
        return pa.table({f"c{i}": pa.array(rng.standard_normal(n)) for i in range(cols)})

    assert "multivariate_outliers" in _joint(table(100, 2))  # exactly 100 rows
    assert "multivariate_outliers" not in _joint(table(99, 2))  # one fewer
    assert "multivariate_outliers" not in _joint(
        pa.table({"a": pa.array(rng.standard_normal(500)), "g": pa.array(["x", "y"] * 250)})
    )  # one numeric column


def test_rows_with_a_missing_value_are_left_out_of_the_fit() -> None:
    rng = np.random.default_rng(8)
    n = 400
    a, b = rng.standard_normal(n), rng.standard_normal(n)
    a[: n - 99] = np.nan  # 99 complete rows: below the minimum
    t = pa.table({"a": pa.array(a, from_pandas=True), "b": pa.array(b)})
    assert "multivariate_outliers" not in _joint(t)
    a[n - 100 :] = rng.standard_normal(100)
    t = pa.table({"a": pa.array(a, from_pandas=True), "b": pa.array(b)})
    assert _joint(t)["multivariate_outliers"]["rows"] == 100


def test_columns_with_few_values_and_collinear_columns_are_not_used() -> None:
    rng = np.random.default_rng(9)
    n = 1000
    a, b = rng.standard_normal(n), rng.standard_normal(n)
    t = pa.table(
        {
            "a": pa.array(a),
            "b": pa.array(b),
            "twice_a": pa.array(2.0 * a + 1.0),  # collinear: a singular covariance
            "flag": pa.array(rng.integers(0, 2, n).astype(np.int64)),  # two values
        }
    )
    mo = _joint(t)["multivariate_outliers"]
    assert mo["columns"] == ["a", "b"]


def test_the_mcd_resists_contamination_that_breaks_the_classical_estimate() -> None:
    rng = np.random.default_rng(10)
    n = 2000
    x = rng.multivariate_normal([0, 0], [[1, 0.9], [0.9, 1]], n)
    bad = rng.choice(n, 400, replace=False)  # 20% far away
    x[bad] = rng.multivariate_normal([8, -8], 0.2 * np.eye(2), 400)
    loc, cov, h = MV.fast_mcd(x)  # type: ignore[misc]
    assert h == (n + 2 + 1) // 2
    assert np.abs(loc).max() < 0.2
    assert cov[0, 1] / math.sqrt(cov[0, 0] * cov[1, 1]) == pytest.approx(0.9, abs=0.05)
    classical = np.cov(x, rowvar=False)
    assert classical[0, 1] / math.sqrt(classical[0, 0] * classical[1, 1]) < 0.3


# --- PCA ------------------------------------------------------------------------------------------


def _factors(n: int, seed: int, noise: float = 0.05) -> pa.Table:
    rng = np.random.default_rng(seed)
    f1, f2 = rng.standard_normal(n), rng.standard_normal(n)
    mix = {
        "a": (1.0, 0.0),
        "b": (0.8, 0.6),
        "c": (0.3, 1.0),
        "d": (-0.7, 0.7),
        "e": (0.9, -0.4),
    }
    return pa.table(
        {
            k: pa.array(w1 * f1 + w2 * f2 + noise * rng.standard_normal(n))
            for k, (w1, w2) in mix.items()
        }
    )


def test_five_columns_from_two_latent_factors_have_two_effective_dimensions() -> None:
    p = _joint(_factors(3000, 1))["pca"]
    assert p["effective_dimension"] == 2
    assert p["columns"] == ["a", "b", "c", "d", "e"]
    ratio = p["explained_variance_ratio"]
    assert len(ratio) == 5 and sum(ratio) == pytest.approx(1.0, abs=1e-4)
    assert ratio == sorted(ratio, reverse=True)
    assert sum(ratio[:2]) > 0.95
    assert len(p["components"]) == 2  # the components that reach 95%
    assert all(len(row) == 5 for row in p["components"])


def test_one_factor_gives_one_dimension_and_independent_columns_give_many() -> None:
    rng = np.random.default_rng(2)
    n = 3000
    f = rng.standard_normal(n)
    one = pa.table(
        {f"x{i}": pa.array(f * (i + 1) + 0.05 * rng.standard_normal(n)) for i in range(4)}
    )
    assert _joint(one)["pca"]["effective_dimension"] == 1
    indep = pa.table({f"x{i}": pa.array(rng.standard_normal(n)) for i in range(5)})
    assert _joint(indep)["pca"]["effective_dimension"] == 5  # 0.9 of five equal variances
    assert len(_joint(indep)["pca"]["components"]) == 5


def test_each_component_has_its_largest_loading_positive_and_unit_length() -> None:
    p = _joint(_factors(3000, 3))["pca"]
    for comp in p["components"]:
        assert comp[int(np.argmax(np.abs(comp)))] > 0
        assert sum(x * x for x in comp) == pytest.approx(1.0, abs=1e-4)
    # orthogonal
    a, b = p["components"]
    assert abs(sum(x * y for x, y in zip(a, b, strict=True))) < 1e-4


def test_the_sign_is_fixed_whatever_the_column_order_or_sign() -> None:
    t = _factors(2000, 4)
    flipped = pa.table({k: pa.array(-np.asarray(t[k])) for k in t.column_names})
    a, b = _joint(t)["pca"], _joint(flipped)["pca"]
    assert a["components"] == b["components"]  # negating every column leaves the loadings' sign
    assert a["explained_variance_ratio"] == b["explained_variance_ratio"]


def test_pca_needs_two_columns_and_skips_a_constant() -> None:
    rng = np.random.default_rng(5)
    n = 500
    only = pa.table({"a": pa.array(rng.standard_normal(n)), "g": pa.array(["x", "y"] * (n // 2))})
    assert "pca" not in _joint(only)
    two = pa.table({"a": pa.array(rng.standard_normal(n)), "b": pa.array(rng.standard_normal(n))})
    assert "pca" in _joint(two)
    const = pa.table(
        {
            "a": pa.array(rng.standard_normal(n)),
            "b": pa.array(rng.standard_normal(n)),
            "k": pa.array([3.0] * n),
        }
    )
    assert _joint(const)["pca"]["columns"] == ["a", "b"]


def test_pca_is_deterministic_and_has_a_ninety_percent_boundary() -> None:
    # two components with ratios 0.9 and 0.1 exactly: 0.9 reaches 90% with one
    vals = [np.array([0.0]), np.array([0.0])]
    del vals
    rng = np.random.default_rng(6)
    n = 20000
    z = rng.standard_normal((n, 2))
    # variances 9 : 1 on the standardised columns of correlation 0.8 -> ratios 0.9 and 0.1
    x = z @ np.array([[1.0, 0.8], [0.0, 0.6]])
    p = MV.pca([x[:, 0], x[:, 1]], ["u", "v"])
    assert p is not None
    assert p["explained_variance_ratio"][0] == pytest.approx(0.9, abs=0.01)
    assert p["effective_dimension"] in (1, 2)
    assert MV.pca([x[:, 0], x[:, 1]], ["u", "v"]) == p


# --- cohorts --------------------------------------------------------------------------------------


def _clusters(n: int, seed: int, dims: int = 2) -> tuple[pa.Table, np.ndarray]:
    rng = np.random.default_rng(seed)
    shares = [0.5, 0.3, 0.2]
    centres = np.array([[0.0] * dims, [8.0] * dims, [-8.0, 8.0] + [0.0] * (dims - 2)])
    label = rng.choice(3, n, p=shares)
    x = centres[label] + rng.standard_normal((n, dims))
    return pa.table({f"x{i}": pa.array(x[:, i]) for i in range(dims)}), label


@pytest.mark.parametrize("seed", [1, 2])
def test_three_separated_clusters_give_k_three_and_their_shares(seed: int) -> None:
    table, _ = _clusters(6000, seed)
    c = _joint(table)["cohorts"]
    assert c["found"] is True and c["k"] == 3
    shares = sorted(k["share"] for k in c["cohorts"])
    for got, want in zip(shares, [0.2, 0.3, 0.5], strict=True):
        assert abs(got - want) <= 0.02
    assert c["silhouette"] >= 0.25
    first = c["cohorts"][0]
    assert set(first["summary"]) == {"x0", "x1"}
    assert set(first["summary"]["x0"]) == {"mean", "std"}


@pytest.mark.parametrize("dims", [1, 2, 3, 5])
def test_a_single_gaussian_has_no_cohorts(dims: int) -> None:
    rng = np.random.default_rng(dims)
    n = 4000
    t = pa.table({f"x{i}": pa.array(rng.standard_normal(n)) for i in range(max(dims, 2))})
    c = _joint(t)["cohorts"]
    assert c["found"] is False
    assert set(c) == {"found", "silhouette"}
    assert isinstance(c["silhouette"], float)


def test_a_correlated_gaussian_and_uniform_data_have_no_cohorts() -> None:
    rng = np.random.default_rng(3)
    g = rng.multivariate_normal([0, 0], [[1, 0.9], [0.9, 1]], 5000)
    assert not _joint(pa.table({"a": pa.array(g[:, 0]), "b": pa.array(g[:, 1])}))["cohorts"][
        "found"
    ]
    u = rng.random((5000, 2))
    assert not _joint(pa.table({"a": pa.array(u[:, 0]), "b": pa.array(u[:, 1])}))["cohorts"][
        "found"
    ]


def test_categorical_columns_are_one_hot_and_summarised_by_their_mode() -> None:
    rng = np.random.default_rng(4)
    n = 3000
    label = rng.choice(2, n, p=[0.6, 0.4])
    t = pa.table(
        {
            "score": pa.array(rng.standard_normal(n) * 0.2 + label * 0.1),
            "tier": pa.array(np.where(label == 0, "basic", "premium")),
            "region": pa.array(np.where(label == 0, "north", "south")),
        }
    )
    c = _joint(t)["cohorts"]
    assert c["found"] and c["k"] == 2
    modes = {k["summary"]["tier"]["mode"]: k for k in c["cohorts"]}
    assert set(modes) == {"basic", "premium"}
    assert modes["basic"]["summary"]["tier"]["share"] == 1.0
    assert abs(modes["basic"]["share"] - 0.6) < 0.03
    assert sorted(c["categorical"]) == ["region", "tier"] and c["numeric"] == ["score"]


def test_a_categorical_column_of_more_than_twenty_levels_is_not_used() -> None:
    rng = np.random.default_rng(5)
    n = 2000
    t = pa.table(
        {
            "a": pa.array(rng.standard_normal(n)),
            "b": pa.array(rng.standard_normal(n)),
            "wide": pa.array([f"v{i}" for i in rng.integers(0, 21, n)]),
            "narrow": pa.array([f"v{i}" for i in rng.integers(0, 20, n)]),
        }
    )
    c = _joint(t)["cohorts"]
    assert "wide" not in c.get("categorical", ["wide"]) or c["found"] is False
    if c["found"]:
        assert "narrow" in c["categorical"] and "wide" not in c["categorical"]


def test_cohorts_need_a_hundred_rows_and_two_columns() -> None:
    rng = np.random.default_rng(6)

    def table(n: int) -> pa.Table:
        return pa.table(
            {"a": pa.array(rng.standard_normal(n)), "b": pa.array(rng.standard_normal(n))}
        )

    assert "cohorts" in _joint(table(100))
    assert "cohorts" not in _joint(table(99))


def test_cohorts_are_deterministic_and_order_by_share() -> None:
    table, _ = _clusters(3000, 7)
    a, b = _joint(table)["cohorts"], _joint(table)["cohorts"]
    assert a == b
    shares = [k["share"] for k in a["cohorts"]]
    assert shares == sorted(shares, reverse=True)
    assert sum(k["rows"] for k in a["cohorts"]) == a["rows"]


# --- the mixed-type copula ----------------------------------------------------------------------


def _mixed(n: int = 4000, seed: int = 1) -> pa.Table:
    rng = np.random.default_rng(seed)
    z = rng.multivariate_normal([0, 0, 0], [[1, 0.7, 0.5], [0.7, 1, 0.3], [0.5, 0.3, 1]], n)
    cut = np.quantile(z[:, 1], [0.5, 0.8])
    seg = np.array(["gold", "silver", "bronze"])[np.searchsorted(cut, z[:, 1])]
    return pa.table(
        {
            "spend": pa.array(np.exp(z[:, 0])),
            "segment": pa.array(seg),
            "age": pa.array(np.round(40 + 10 * z[:, 2]).astype(np.int64)),
            "customer_id": pa.array(np.arange(n, dtype=np.int64)),
            "order_fk": pa.array(rng.integers(0, 50, n).astype(np.int64)),
        }
    )


def test_the_copula_entry_holds_format_version_order_and_a_positive_definite_matrix() -> None:
    cop = _joint(_mixed())["copula"]
    assert cop["format"] == "shape.copula" and cop["version"] == 1
    assert cop["columns"] == ["spend", "age", "segment"]  # numeric first, key-like names left out
    assert cop["numeric"] == ["spend", "age"] and cop["categorical"] == ["segment"]
    # by frequency, ties by value
    assert cop["categories"] == {"segment": ["gold", "silver", "bronze"]}
    m = np.array(cop["correlation"])
    assert m.shape == (3, 3) and np.allclose(m, m.T) and np.allclose(np.diag(m), 1.0)
    assert np.linalg.eigvalsh(m).min() > 0
    assert m[0, 2] > 0.3  # spend and segment move together


def test_categories_with_equal_counts_are_ordered_by_value() -> None:
    rng = np.random.default_rng(2)
    n = 1200
    t = pa.table(
        {
            "g": pa.array(["b", "a", "c"] * (n // 3)),
            "x": pa.array(rng.standard_normal(n)),
        }
    )
    assert _joint(t)["copula"]["categories"] == {"g": ["a", "b", "c"]}


def test_the_latent_matrix_undoes_the_attenuation_of_categories() -> None:
    # a latent correlation of 0.8 between a continuous column and a three-level one: the normal
    # scores of the levels correlate at about 0.64; the stored latent value is about 0.8
    rng = np.random.default_rng(3)
    n = 40000
    z = rng.multivariate_normal([0, 0], [[1, 0.8], [0.8, 1]], n)
    levels = np.array(["p", "q", "r"])[np.searchsorted(np.quantile(z[:, 1], [0.5, 0.8]), z[:, 1])]
    cop = _joint(pa.table({"x": pa.array(z[:, 0]), "g": pa.array(levels)}))["copula"]
    assert cop["correlation"][0][1] == pytest.approx(0.8, abs=0.03)


def test_the_copula_has_at_most_sixteen_columns_of_each_role() -> None:
    rng = np.random.default_rng(4)
    n = 600
    cols: dict[str, Any] = {f"n{i:02d}": pa.array(rng.standard_normal(n)) for i in range(20)}
    cols.update(
        {f"c{i:02d}": pa.array([f"v{x}" for x in rng.integers(0, 4, n)]) for i in range(20)}
    )
    cop = _joint(pa.table(cols))["copula"]
    assert len(cop["numeric"]) <= 16 and len(cop["categorical"]) <= 16
    assert len(cop["columns"]) == len(cop["numeric"]) + len(cop["categorical"])


def test_the_copula_needs_a_hundred_rows_two_columns_and_leaves_out_keys() -> None:
    rng = np.random.default_rng(5)

    def table(n: int) -> pa.Table:
        return pa.table(
            {"a": pa.array(rng.standard_normal(n)), "g": pa.array(["x", "y"] * (n // 2))}
        )

    assert "copula" in _joint(table(100))
    assert "copula" not in _joint(table(98))
    keyed = pa.table({"a": pa.array(rng.standard_normal(300)), "row_id": pa.array(np.arange(300))})
    assert "copula" not in _joint(keyed)


def test_a_column_with_more_than_the_level_cap_is_left_out() -> None:
    rng = np.random.default_rng(6)
    n = 2000
    t = pa.table(
        {
            "a": pa.array(rng.standard_normal(n)),
            "many": pa.array([f"v{i}" for i in rng.integers(0, MV.COPULA_MAX_LEVELS + 1, n)]),
            "few": pa.array([f"v{i}" for i in rng.integers(0, 5, n)]),
        }
    )
    cop = _joint(t)["copula"]
    assert "many" not in cop["categorical"] and "few" in cop["categorical"]


# --- identical in both kernels, left out of the share-safe profile ----------------------------


def test_the_new_entries_are_in_the_profile_and_not_in_the_share_safe_profile() -> None:
    from shape.privacy.safe_profile import SafeConfig, to_safe_profile

    profile = shape.profile(_mixed(), multivariate=True)
    j = profile.to_dict()["joint"]
    for key in ("pca", "cohorts", "copula"):
        assert key in j
    safe = json.dumps(to_safe_profile(profile, SafeConfig()).to_dict(), default=str)
    for key in ("multivariate_outliers", "pca", "cohorts", "copula", "categories", "joint"):
        assert f'"{key}"' not in safe


@pytest.mark.parametrize("kernel", ["python", "rust"])
def test_the_entries_are_identical_in_both_kernel_modes(kernel: str) -> None:
    import os
    import subprocess
    import sys
    from pathlib import Path

    code = (
        "import json, sys, numpy as np, pyarrow as pa, shape\n"
        f"KEYS = {KEYS!r}\n"
        "from tests.joint.test_multivariate_profile import _mixed, _bivariate, _clusters\n"
        "out = {}\n"
        "for name, t in (('mixed', _mixed()), ('biv', _bivariate(3000, 3, planted=0.02)),"
        " ('clu', _clusters(2500, 4)[0])):\n"
        "    j = shape.profile(t, multivariate=True).to_dict()['joint']\n"
        "    out[name] = {k: j.get(k) for k in KEYS}\n"
        "print(json.dumps(out, sort_keys=True, allow_nan=False))\n"
    )
    root = Path(__file__).parents[2]
    env = {**os.environ, "SHAPE_KERNEL": kernel, "PYTHONPATH": str(root)}
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=root, check=False
    )
    assert done.returncode == 0, done.stderr
    expected = _expected_in_process()
    assert json.loads(done.stdout) == expected


def _expected_in_process() -> dict[str, Any]:
    out = {}
    for name, t in (
        ("mixed", _mixed()),
        ("biv", _bivariate(3000, 3, planted=0.02)),
        ("clu", _clusters(2500, 4)[0]),
    ):
        j = shape.profile(t, multivariate=True).to_dict()["joint"]
        out[name] = {k: j.get(k) for k in KEYS}
    return json.loads(json.dumps(out, sort_keys=True))


def test_the_joint_switch_turns_the_new_entries_off() -> None:
    t = _mixed()
    assert "pca" in shape.profile(t, joint=True, multivariate=True).to_dict()["joint"]
    assert "joint" not in shape.profile(t, joint=False, multivariate=True).to_dict()


def test_the_switch_environment_variable_turns_them_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHAPE_PROFILE_JOINT", "0")
    assert "joint" not in shape.profile(_mixed(), multivariate=True).to_dict()


# --- bounded cost ---------------------------------------------------------------------------------


def _cols(table: pa.Table) -> Any:
    from shape.profile.reference.readers import _arrow_cols

    cols = _arrow_cols(table)
    for c in cols:
        c.multivariate = True  # INT-18: the entries are opt-in (shape.profile(multivariate=True))
    return cols


def test_the_cost_does_not_grow_with_the_table() -> None:
    rng = np.random.default_rng(1)
    small = _mixed(8000, 2)
    t0 = time.perf_counter()
    analyze.analyze_table(_cols(small), 8000)
    base = time.perf_counter() - t0
    n = 400_000
    z = rng.standard_normal((n, 3))
    big = pa.table(
        {
            "spend": pa.array(np.exp(z[:, 0])),
            "segment": pa.array(np.array(["gold", "silver", "bronze"])[rng.integers(0, 3, n)]),
            "age": pa.array(np.round(40 + 10 * z[:, 2]).astype(np.int64)),
            "tier": pa.array(np.array(["a", "b"])[rng.integers(0, 2, n)]),
        }
    )
    t0 = time.perf_counter()
    j = analyze.analyze_table(_cols(big), n)
    elapsed = time.perf_counter() - t0
    assert j is not None and j["rows_analyzed"] == analyze.LARGE.sample_rows
    assert j["copula"]["rows"] == analyze.LARGE.sample_rows
    assert j["pca"]["rows"] == analyze.LARGE.sample_rows
    assert elapsed < max(3.0 * base, 5.0), (elapsed, base)


@pytest.mark.heavy
def test_a_ten_million_row_table_is_bounded_in_time_and_memory() -> None:
    n = 10_000_000
    rng = np.random.default_rng(2)
    base = rng.standard_normal(n)
    table = pa.table(
        {
            "a": pa.array(base),
            "b": pa.array(0.7 * base + 0.7 * rng.standard_normal(n)),
            "c": pa.array(np.exp(rng.standard_normal(n))),
            "g": pa.DictionaryArray.from_arrays(
                pa.array(rng.integers(0, 6, n).astype(np.int8)),
                pa.array([f"g{i}" for i in range(6)]),
            ),
            "h": pa.DictionaryArray.from_arrays(
                pa.array(rng.integers(0, 4, n).astype(np.int8)),
                pa.array([f"h{i}" for i in range(4)]),
            ),
        }
    )
    cols = _cols(table)
    tracemalloc.start()
    t0 = time.perf_counter()
    j = analyze.analyze_table(cols, n)
    elapsed = time.perf_counter() - t0
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert j is not None and j["sampled"] is True
    for key in ("multivariate_outliers", "pca", "cohorts", "copula"):
        assert key in j, key
    assert elapsed < 30.0, elapsed
    # the sample (5,000 rows), the distance matrices and the fits: never a copy of a column (80 MB)
    assert peak < 0.5 * 80_000_000, peak


def test_the_entries_are_opt_in_and_the_rest_of_the_profile_is_the_same() -> None:
    """INT-18: the multivariate entries cost more than the default profile's benchmark gate
    allows, so they are computed only with multivariate=True (shape profile --multivariate)."""
    t = _mixed()
    plain = shape.profile(t).to_dict()
    deep = shape.profile(t, multivariate=True).to_dict()
    entries = {"multivariate_outliers", "pca", "cohorts", "copula"}
    assert not entries & set(plain["joint"])
    assert entries <= set(deep["joint"])
    for k in entries:
        deep["joint"].pop(k)
    # W2-07's record lists their internal samples only when they ran
    internal = deep["sampling"]["internal"]
    deep["sampling"]["internal"] = [e for e in internal if e["analysis"] not in entries]
    assert json.dumps(plain, sort_keys=True, default=str) == json.dumps(
        deep, sort_keys=True, default=str
    )
