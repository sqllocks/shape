"""W3-08 (#232): ``multivariate_outlier_rate_change``, ``structure_change`` and ``cohort_shift`` in
``shape diff``: known answers, boundaries, sampling noise and older profiles."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pytest

import shape
from shape.drift.engine import DEFAULT_THRESHOLDS, KIND_SEVERITY, TableView, resolve_policy
from shape.drift.multivariate import (
    match_cohorts,
    outlier_rate_noise,
    principal_angle,
    subspace_noise_angle,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "profiles"
KINDS = ("multivariate_outlier_rate_change", "structure_change", "cohort_shift")


def _kinds(result: Any) -> list[str]:
    return [c["kind"] for c in result.changes if c["kind"] in KINDS]


def _biv(n: int, seed: int, planted: float = 0.0) -> pa.Table:
    rng = np.random.default_rng(seed)
    x = rng.multivariate_normal([50.0, 200.0], [[100.0, 270.0], [270.0, 900.0]], n)
    k = int(round(n * planted))
    if k:
        rows = rng.choice(n, k, replace=False)
        sign = rng.choice([-1.0, 1.0], k)
        x[rows, 0] = 50.0 + sign * rng.uniform(12.0, 18.0, k)
        x[rows, 1] = 200.0 - sign * rng.uniform(36.0, 54.0, k)
    return pa.table({"age": pa.array(x[:, 0]), "income": pa.array(x[:, 1])})


# --- registered ---------------------------------------------------------------------------------


def test_the_kinds_and_thresholds_are_registered_with_the_documented_values() -> None:
    assert DEFAULT_THRESHOLDS["multivariate_outlier_rate"] == 0.02
    assert DEFAULT_THRESHOLDS["structure_angle"] == 30.0
    assert DEFAULT_THRESHOLDS["cohort_tvd"] == 0.10
    for kind in KINDS:
        assert KIND_SEVERITY[kind] == "low"


# --- multivariate outliers ------------------------------------------------------------------------


def test_a_rise_in_the_share_of_multivariate_outliers_is_reported() -> None:
    result = shape.diff(
        shape.profile(_biv(5000, 1), multivariate=True),
        shape.profile(_biv(5000, 2, planted=0.06), multivariate=True),
    )
    (change,) = [c for c in result.changes if c["kind"] == "multivariate_outlier_rate_change"]
    assert change["severity"] == "low"
    assert change["baseline"] < 0.005 and 0.05 < change["current"] < 0.08
    assert change["detail"]["columns"] == ["age", "income"]
    assert "age, income" in change["message"] and "%" in change["message"]
    assert change["score"] == pytest.approx(change["current"] - change["baseline"], abs=1e-3)


def test_a_rise_below_the_threshold_is_not_reported_and_the_threshold_is_settable() -> None:
    base, cur = (
        shape.profile(_biv(5000, 1), multivariate=True),
        shape.profile(_biv(5000, 2, planted=0.015), multivariate=True),
    )
    assert "multivariate_outlier_rate_change" not in _kinds(shape.diff(base, cur))  # rise ~0.016
    result = shape.diff(base, cur, thresholds={"multivariate_outlier_rate": 0.005})
    assert "multivariate_outlier_rate_change" in _kinds(result)


def test_a_fall_in_the_share_is_not_reported() -> None:
    base, cur = (
        shape.profile(_biv(5000, 2, planted=0.06), multivariate=True),
        shape.profile(_biv(5000, 1), multivariate=True),
    )
    assert "multivariate_outlier_rate_change" not in _kinds(shape.diff(base, cur))


def test_the_rise_never_goes_below_the_sampling_noise_of_the_two_rates() -> None:
    # 120 rows each: 8% planted is a rise of 0.08, inside six binomial standard errors (0.15)
    base, cur = (
        shape.profile(_biv(120, 1), multivariate=True),
        shape.profile(_biv(120, 2, planted=0.08), multivariate=True),
    )
    assert outlier_rate_noise(0.0, 120, 0.08, 120) > 0.08
    assert "multivariate_outlier_rate_change" not in _kinds(shape.diff(base, cur))
    # a threshold of zero does not lower the noise floor
    assert "multivariate_outlier_rate_change" not in _kinds(
        shape.diff(base, cur, thresholds={"multivariate_outlier_rate": 0.0})
    )


def test_the_noise_floor_shrinks_with_the_sample() -> None:
    assert outlier_rate_noise(0.01, 20000, 0.02, 20000) < 0.02
    assert outlier_rate_noise(0.01, 100, 0.02, 100) > outlier_rate_noise(0.01, 10000, 0.02, 10000)


def test_ignored_columns_and_different_column_sets_suppress_the_outlier_change() -> None:
    base, cur = (
        shape.profile(_biv(5000, 1), multivariate=True),
        shape.profile(_biv(5000, 2, planted=0.06), multivariate=True),
    )
    assert "multivariate_outlier_rate_change" not in _kinds(
        shape.diff(base, cur, ignore_columns=["income"])
    )
    other = _biv(5000, 2, planted=0.06).append_column(
        "extra", pa.array(np.random.default_rng(0).standard_normal(5000))
    )
    assert "multivariate_outlier_rate_change" not in _kinds(
        shape.diff(base, shape.profile(other, multivariate=True))
    )


@pytest.mark.parametrize("seed", range(8))
def test_two_samples_of_one_table_do_not_show_an_outlier_change(seed: int) -> None:
    a, b = (
        shape.profile(_biv(2000, seed), multivariate=True),
        shape.profile(_biv(2000, seed + 100), multivariate=True),
    )
    assert not _kinds(shape.diff(a, b))
    skew = np.random.default_rng(seed)

    def table() -> pa.Table:
        return pa.table(
            {
                "x": pa.array(skew.lognormal(3, 0.8, 1500)),
                "y": pa.array(skew.gamma(2, 10, 1500)),
                "z": pa.array(skew.normal(0, 1, 1500)),
            }
        )

    assert not _kinds(
        shape.diff(
            shape.profile(table(), multivariate=True), shape.profile(table(), multivariate=True)
        )
    )


# --- structure ------------------------------------------------------------------------------------


def _factors(n: int, seed: int, layout: str) -> pa.Table:
    rng = np.random.default_rng(seed)
    f1, f2 = rng.standard_normal(n), rng.standard_normal(n)
    noise = lambda: 0.1 * rng.standard_normal(n)  # noqa: E731
    if layout == "pairs":  # a, b load on f1; c, d on f2
        cols = {"a": f1 + noise(), "b": f1 + noise(), "c": f2 + noise(), "d": f2 + noise()}
    elif layout == "crossed":  # a, c load on f1; b, d on f2
        cols = {"a": f1 + noise(), "c": f1 + noise(), "b": f2 + noise(), "d": f2 + noise()}
        cols = {k: cols[k] for k in ("a", "b", "c", "d")}
    else:  # independent
        cols = {k: rng.standard_normal(n) for k in "abcd"}
    return pa.table({k: pa.array(v) for k, v in cols.items()})


def test_a_change_of_two_effective_dimensions_is_reported() -> None:
    result = shape.diff(
        shape.profile(_factors(3000, 1, "pairs"), multivariate=True),
        shape.profile(_factors(3000, 2, "independent"), multivariate=True),
    )
    (change,) = [c for c in result.changes if c["kind"] == "structure_change"]
    assert change["severity"] == "low"
    assert (change["baseline"], change["current"]) == (2, 4)
    assert change["detail"]["reason"] == "dimension"


def test_a_turned_subspace_of_the_same_dimension_is_reported() -> None:
    result = shape.diff(
        shape.profile(_factors(3000, 1, "pairs"), multivariate=True),
        shape.profile(_factors(3000, 2, "crossed"), multivariate=True),
    )
    (change,) = [c for c in result.changes if c["kind"] == "structure_change"]
    assert change["baseline"] == change["current"] == 2
    assert change["detail"]["reason"] == "subspace"
    assert change["detail"]["angle_degrees"] > 30.0
    assert "degrees" in change["message"]


@pytest.mark.parametrize("seed", range(10))
def test_two_samples_of_one_structure_report_nothing(seed: int) -> None:
    for layout in ("pairs", "independent"):
        a = shape.profile(_factors(1500, seed, layout), multivariate=True)
        b = shape.profile(_factors(1500, seed + 50, layout), multivariate=True)
        assert "structure_change" not in _kinds(shape.diff(a, b)), (layout, seed)


def _pca(
    columns: list[str], ratios: list[float], comps: list[list[float]], rows: int = 5000
) -> Any:
    cum = np.cumsum(ratios)
    eff = int(np.searchsorted(cum, 0.9 - 1e-12) + 1)
    return {
        "columns": columns,
        "rows": rows,
        "explained_variance_ratio": ratios,
        "components": comps,
        "effective_dimension": eff,
    }


def _view(joint: dict[str, Any]) -> TableView:
    return TableView(rows=1000, columns={}, joint=joint)


def _structure(base: dict[str, Any], cur: dict[str, Any], **thresholds: float) -> list[str]:
    from shape.drift.multivariate import _structure as run

    policy = resolve_policy(thresholds or None)
    return [
        r["kind"]
        for r in run(None, _view({"pca": base}), _view({"pca": cur}), policy.thresholds, policy)
    ]


def test_the_dimension_step_boundary_is_two_components() -> None:
    cols = list("abcdef")
    ident = np.eye(6).tolist()

    def entry(ratios: list[float]) -> dict[str, Any]:
        return _pca(cols, ratios, ident)

    base = entry([0.4, 0.3, 0.25, 0.02, 0.02, 0.01])  # 0.9 at the third
    one_more = entry([0.35, 0.25, 0.2, 0.15, 0.03, 0.02])  # fourth
    two_more = entry([0.25, 0.2, 0.18, 0.15, 0.12, 0.10])  # sixth: cum .9 at 5? 0.9 -> fifth
    assert base["effective_dimension"] == 3 and one_more["effective_dimension"] == 4
    assert two_more["effective_dimension"] == 5
    assert _structure(base, one_more) == []
    assert _structure(base, two_more) == ["structure_change"]


def test_the_angle_threshold_boundary_and_setting() -> None:
    cols = ["a", "b", "c"]
    ratios = [0.62, 0.30, 0.08]
    rows = 10**7  # no sampling noise to speak of

    def comp(theta: float) -> list[list[float]]:
        t = np.radians(theta)
        return [[np.cos(t), np.sin(t), 0.0], [-np.sin(t), np.cos(t), 0.0]]

    base = _pca(cols, ratios, [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], rows)

    # the leading subspace is the plane of a and b: rotating inside it changes nothing; tilting
    # a toward c turns it
    def tilt(theta: float) -> list[list[float]]:
        t = np.radians(theta)
        return [[1.0, 0.0, 0.0], [0.0, np.cos(t), np.sin(t)]]

    at_29 = _pca(cols, ratios, tilt(29.0), rows)
    at_31 = _pca(cols, ratios, tilt(31.0), rows)
    assert principal_angle(base, at_29, 2) is not None
    assert _structure(base, _pca(cols, ratios, comp(40.0), rows)) == []
    assert _structure(base, at_29) == []
    assert _structure(base, at_31) == ["structure_change"]
    assert _structure(base, at_29, structure_angle=20.0) == ["structure_change"]
    assert _structure(base, at_31, structure_angle=35.0) == []


def test_a_flat_spectrum_has_noise_of_ninety_degrees_so_the_subspace_is_never_compared() -> None:
    assert subspace_noise_angle([0.25, 0.25, 0.25, 0.25], 2, 5000, 5000) == 90.0
    clear = subspace_noise_angle([0.7, 0.2, 0.05, 0.05], 2, 5000, 5000)
    assert clear < 5.0
    assert subspace_noise_angle([0.7, 0.2, 0.05, 0.05], 2, 50, 50) > clear


def test_the_pca_of_columns_that_only_partly_match_compares_the_shared_ones() -> None:
    ident = [[1, 0, 0, 0], [0, 1, 0, 0]]
    a = _pca(["a", "b", "c", "d"], [0.6, 0.3, 0.07, 0.03], ident)
    b = _pca(["a", "b", "c", "z"], [0.6, 0.3, 0.07, 0.03], ident)
    got = principal_angle(a, b, 2)
    assert got is not None and got[1] == ["a", "b", "c"] and got[0] < 1e-6
    assert principal_angle(a, _pca(["a", "q", "r", "z"], [0.6, 0.3, 0.07, 0.03], ident), 2) is None
    assert principal_angle(a, b, 3) is None  # the three shared columns are the whole space


# --- cohorts --------------------------------------------------------------------------------------


def _clusters(n: int, seed: int, shares: tuple[float, float, float] = (0.5, 0.3, 0.2)) -> pa.Table:
    rng = np.random.default_rng(seed)
    centres = np.array([[0.0, 0.0], [8.0, 8.0], [-8.0, 8.0]])
    label = rng.choice(3, n, p=list(shares))
    x = centres[label] + rng.standard_normal((n, 2))
    return pa.table({"x0": pa.array(x[:, 0]), "x1": pa.array(x[:, 1])})


def test_a_swap_of_cohort_shares_is_a_shift_matched_by_nearest_centroid() -> None:
    base = shape.profile(_clusters(6000, 1, (0.5, 0.3, 0.2)), multivariate=True)
    cur = shape.profile(_clusters(6000, 2, (0.2, 0.3, 0.5)), multivariate=True)
    (change,) = [c for c in shape.diff(base, cur).changes if c["kind"] == "cohort_shift"]
    assert change["severity"] == "low"
    assert change["detail"]["total_variation_distance"] == pytest.approx(0.3, abs=0.03)
    assert sorted(change["detail"]["matched"]) == [0, 1, 2]  # each baseline cohort found its twin
    assert "total variation distance" in change["message"]


def test_the_threshold_boundary_and_setting() -> None:
    base = shape.profile(_clusters(20000, 1, (0.5, 0.3, 0.2)), multivariate=True)
    small = shape.profile(_clusters(20000, 2, (0.45, 0.3, 0.25)), multivariate=True)  # 0.05
    large = shape.profile(_clusters(20000, 3, (0.35, 0.3, 0.35)), multivariate=True)  # 0.15
    assert "cohort_shift" not in _kinds(shape.diff(base, small))
    assert "cohort_shift" in _kinds(shape.diff(base, large))
    assert "cohort_shift" in _kinds(shape.diff(base, small, thresholds={"cohort_tvd": 0.03}))
    assert "cohort_shift" not in _kinds(shape.diff(base, large, thresholds={"cohort_tvd": 0.2}))


def test_the_shift_never_goes_below_the_sampling_noise() -> None:
    # 150 rows: a move of 0.12 in the shares is inside three standard errors summed over cohorts
    base = shape.profile(_clusters(150, 1, (0.5, 0.3, 0.2)), multivariate=True)
    cur = shape.profile(_clusters(150, 2, (0.38, 0.3, 0.32)), multivariate=True)
    assert "cohort_shift" not in _kinds(shape.diff(base, cur, thresholds={"cohort_tvd": 0.0}))


@pytest.mark.parametrize("seed", range(8))
def test_two_samples_of_one_mixture_report_no_shift(seed: int) -> None:
    a, b = (
        shape.profile(_clusters(3000, seed), multivariate=True),
        shape.profile(_clusters(3000, seed + 70), multivariate=True),
    )
    assert "cohort_shift" not in _kinds(shape.diff(a, b))


def test_no_cohorts_on_either_side_means_no_cohort_shift() -> None:
    rng = np.random.default_rng(1)

    def gauss(seed: int) -> pa.Table:
        r = np.random.default_rng(seed)
        return pa.table(
            {"x0": pa.array(r.standard_normal(3000)), "x1": pa.array(r.standard_normal(3000))}
        )

    del rng
    assert not _kinds(
        shape.diff(
            shape.profile(gauss(1), multivariate=True), shape.profile(gauss(2), multivariate=True)
        )
    )
    assert "cohort_shift" not in _kinds(
        shape.diff(
            shape.profile(_clusters(4000, 1), multivariate=True),
            shape.profile(gauss(3), multivariate=True),
        )
    )
    assert "cohort_shift" not in _kinds(
        shape.diff(
            shape.profile(gauss(3), multivariate=True),
            shape.profile(_clusters(4000, 1), multivariate=True),
        )
    )


def test_match_cohorts_pairs_each_baseline_cohort_with_the_nearest_current_one() -> None:
    def cohort(share: float, mx: float, mode: str) -> dict[str, Any]:
        return {
            "share": share,
            "summary": {"x": {"mean": mx, "std": 1.0}, "g": {"mode": mode, "share": 0.9}},
        }

    base = [cohort(0.5, 0.0, "a"), cohort(0.5, 10.0, "b")]
    cur = [cohort(0.5, 10.5, "b"), cohort(0.5, -0.5, "a")]
    assert match_cohorts(base, cur, ["x", "g"]) == [1, 0]
    # a categorical mismatch outweighs a small numeric gap
    cur2 = [cohort(0.5, 0.0, "b"), cohort(0.5, 0.2, "a")]
    assert match_cohorts(base, cur2, ["x", "g"])[0] == 1


# --- older profiles ----------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["pre_w3_07.shape", "pre_w3_08.shape"])
def test_an_older_profile_diffs_without_the_new_kinds(name: str) -> None:
    old = shape.load(str(FIXTURES / name))
    table = _clusters(2000, 1)
    new = shape.profile(table, multivariate=True)
    assert not _kinds(shape.diff(old, new))
    assert not _kinds(shape.diff(new, old))
    again = shape.load(str(FIXTURES / name))
    assert not _kinds(shape.diff(old, again))


def test_the_thresholds_are_settable_from_the_command_line(tmp_path: Path) -> None:
    from shape.cli.main import main

    base = shape.profile(_biv(5000, 1), multivariate=True)
    cur = shape.profile(_biv(5000, 2, planted=0.06), multivariate=True)
    # a safe capture keeps no multivariate entry (W1-11 x W3-08): the comparison needs full ones
    shape.save(base, str(tmp_path / "a.shape"), capture="full")
    shape.save(cur, str(tmp_path / "b.shape"), capture="full")
    out = tmp_path / "diff.json"
    code = main(["diff", str(tmp_path / "a.shape"), str(tmp_path / "b.shape"), "--json", str(out)])
    assert code in (0, 1)
    kinds = [c["kind"] for c in json.loads(out.read_text())["changes"]]
    assert "multivariate_outlier_rate_change" in kinds
    out2 = tmp_path / "diff2.json"
    main(
        [
            "diff",
            str(tmp_path / "a.shape"),
            str(tmp_path / "b.shape"),
            "--threshold",
            "multivariate_outlier_rate=0.5",
            "--json",
            str(out2),
        ]
    )
    kinds2 = [c["kind"] for c in json.loads(out2.read_text())["changes"]]
    assert "multivariate_outlier_rate_change" not in kinds2
