"""W3-07 (#103): the diff kinds of the univariate depth fields: ``zero_inflation_change``,
``heaping_change``, ``benford_change`` and ``tail_change``."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pytest

import shape
from shape.drift import univariate as D
from shape.drift.engine import DEFAULT_THRESHOLDS, KIND_SEVERITY, View

N = 5_000
KINDS = ("zero_inflation_change", "heaping_change", "benford_change", "tail_change")
FIELDS = (
    "distribution_candidates",
    "distribution_by_bic",
    "zero_share",
    "zero_inflation",
    "heaping",
    "benford",
    "tail_index",
)


def prof(values: Any, name: str = "x") -> Any:
    return shape.profile(pa.table({name: pa.array(values)}))


def kinds(diff: Any) -> list[str]:
    return [c["kind"] for c in diff.changes]


def only(diff: Any, kind: str) -> list[dict[str, Any]]:
    return [c for c in diff.changes if c["kind"] == kind]


def strip(profile: Any) -> Any:
    """The profile as an older release wrote it: none of the univariate fields."""
    doc = profile.to_dict()
    for col in doc["columns"].values():
        for f in FIELDS:
            col.pop(f, None)
    return type(profile)(doc, name=profile.name)


# --- registration and documentation --------------------------------------------------------------


def test_the_kinds_and_thresholds_are_registered_and_documented() -> None:
    assert {k: KIND_SEVERITY[k] for k in KINDS} == {
        "zero_inflation_change": "medium",
        "heaping_change": "low",
        "benford_change": "medium",
        "tail_change": "low",
    }
    assert DEFAULT_THRESHOLDS["zero_share"] == 0.05
    assert DEFAULT_THRESHOLDS["heaping_ratio"] == 2.0
    assert DEFAULT_THRESHOLDS["benford_class_steps"] == 2
    assert DEFAULT_THRESHOLDS["tail_alpha_drop"] == 0.3
    assert DEFAULT_THRESHOLDS["tail_alpha_max"] == 3.0
    text = (Path(__file__).parents[2] / "docs" / "DRIFT.md").read_text(encoding="utf-8")
    for key in (*KINDS, "zero_share", "heaping_ratio", "benford_class_steps", "tail_alpha_drop"):
        assert f"`{key}`" in text, key
    assert "`tail_alpha_max`" in text


# --- zero_inflation_change -----------------------------------------------------------------------


def _counts(seed: int, zero_share: float = 0.0, lam: float = 3.0, n: int = N) -> list[int]:
    rng = np.random.default_rng(seed)
    return [int(v) for v in np.where(rng.random(n) < zero_share, 0, rng.poisson(lam, n))]


def test_inflation_appearing_is_reported() -> None:
    d = shape.diff(prof(_counts(1)), prof(_counts(2, 0.3)))
    ch = only(d, "zero_inflation_change")
    assert len(ch) == 1 and ch[0]["column"] == "x" and ch[0]["severity"] == "medium"
    assert ch[0]["baseline"] < 0.1 and ch[0]["current"] > 0.3
    assert 0.0 < ch[0]["score"] <= 1.0


def test_the_same_counts_are_quiet_and_so_is_inflation_that_was_already_there() -> None:
    assert only(shape.diff(prof(_counts(1)), prof(_counts(3))), "zero_inflation_change") == []
    both = shape.diff(prof(_counts(4, 0.3)), prof(_counts(5, 0.3)))
    assert only(both, "zero_inflation_change") == []


def test_a_zero_share_move_is_reported_past_the_threshold_and_not_inside_it() -> None:
    def floats(seed: int, zeros: float) -> list[float]:
        rng = np.random.default_rng(seed)
        return [float(v) for v in np.where(rng.random(20_000) < zeros, 0.0, rng.random(20_000) + 1)]

    moved = shape.diff(prof(floats(1, 0.10)), prof(floats(2, 0.17)))
    assert len(only(moved, "zero_inflation_change")) == 1
    inside = shape.diff(prof(floats(1, 0.10)), prof(floats(2, 0.14)))
    assert only(inside, "zero_inflation_change") == []
    loose = shape.diff(prof(floats(1, 0.10)), prof(floats(2, 0.17)), thresholds={"zero_share": 0.2})
    assert only(loose, "zero_inflation_change") == []


def test_zero_inflation_change_is_never_below_sampling_noise() -> None:
    # two samples of 40 rows: a 0.06 move is far inside four standard errors
    a = [0.0] * 4 + [float(i) + 1 for i in range(36)]
    b = [0.0] * 7 + [float(i) + 1 for i in range(33)]
    assert only(shape.diff(prof(a), prof(b)), "zero_inflation_change") == []


# --- heaping_change ------------------------------------------------------------------------------


def _tens(seed: int, share: float, n: int = N) -> list[int]:
    rng = np.random.default_rng(seed)
    return [
        int(v)
        for v in np.where(
            rng.random(n) < share, rng.integers(10, 100, n) * 10, rng.integers(100, 1000, n)
        )
    ]


def test_heaping_appearing_is_reported() -> None:
    ch = only(shape.diff(prof(_tens(1, 0.0)), prof(_tens(2, 0.6))), "heaping_change")
    assert len(ch) == 1 and ch[0]["severity"] == "low"
    assert ch[0]["current"] >= 2.0 and ch[0]["baseline"] < 2.0


def test_a_ratio_that_doubles_is_reported_and_one_that_does_not_is_quiet() -> None:
    base = prof(_tens(1, 0.17))  # ratio about 2.5 on the tens
    assert base.to_dict()["columns"]["x"]["heaping"]["heaped"] is True
    doubled = only(shape.diff(base, prof(_tens(2, 0.6))), "heaping_change")
    assert len(doubled) == 1 and doubled[0]["current"] >= 2 * doubled[0]["baseline"]
    assert only(shape.diff(base, prof(_tens(3, 0.25))), "heaping_change") == []


def _heap(unit: int, observed: float, expected: float, heaped: bool) -> dict[str, Any]:
    return {
        "heaping": {
            "resolution": 1,
            "unit": unit,
            "observed_share": observed,
            "expected_share": expected,
            "ratio": round(observed / expected, 4),
            "heaped": heaped,
        }
    }


def test_a_heaping_flag_that_flips_inside_the_noise_is_quiet() -> None:
    # 400 values each: observed 0.19 against 0.10 expected (ratio 1.9) and 0.21 (2.1) are one
    # sample's width apart on a column heaped right at the threshold
    b = View("float", 400, 0.0, 300, univariate=_heap(10, 0.19, 0.1, False))
    c = View("float", 400, 0.0, 300, univariate=_heap(10, 0.21, 0.1, True))
    assert D.diff_univariate("x", b, c, DEFAULT_THRESHOLDS, True) == []
    far = View("float", 400, 0.0, 300, univariate=_heap(10, 0.6, 0.1, True))
    assert [r["kind"] for r in D.diff_univariate("x", b, far, DEFAULT_THRESHOLDS, True)] == [
        "heaping_change"
    ]


def test_uniform_data_and_the_same_heaping_are_quiet() -> None:
    assert only(shape.diff(prof(_tens(1, 0.0)), prof(_tens(2, 0.0))), "heaping_change") == []
    assert only(shape.diff(prof(_tens(1, 0.5)), prof(_tens(2, 0.5))), "heaping_change") == []
    # heaping that went away is not reported
    assert only(shape.diff(prof(_tens(1, 0.6)), prof(_tens(2, 0.0))), "heaping_change") == []


def test_an_inflation_flag_that_flips_inside_the_noise_is_quiet() -> None:
    def inflation(share: float, inflated: bool) -> dict[str, Any]:
        return {
            "zero_share": share,
            "zero_inflation": {
                "observed": share,
                "poisson_expected": 0.05,
                "nb_expected": 0.06,
                "inflated": inflated,
            },
        }

    # 300 values each: 0.11 to 0.12 is a quarter of a standard error, however the flag fell
    b = View("integer", 300, 0.0, 20, univariate=inflation(0.11, False))
    c = View("integer", 300, 0.0, 20, univariate=inflation(0.12, True))
    assert D.diff_univariate("x", b, c, DEFAULT_THRESHOLDS, True) == []


# --- benford_change ------------------------------------------------------------------------------


def _amounts(seed: int, uniform: bool) -> list[float]:
    rng = np.random.default_rng(seed)
    v = rng.uniform(1.0, 100_000.0, N) if uniform else 10 ** rng.uniform(0.0, 5.0, N)
    return [float(x) for x in v]


def test_conformity_that_collapses_is_reported() -> None:
    ch = only(shape.diff(prof(_amounts(1, False)), prof(_amounts(2, True))), "benford_change")
    assert len(ch) == 1 and ch[0]["baseline"] == "close" and ch[0]["current"] == "nonconformity"
    assert ch[0]["severity"] == "medium" and ch[0]["score"] == pytest.approx(1.0)


def test_the_same_amounts_and_an_improvement_are_quiet() -> None:
    assert (
        only(shape.diff(prof(_amounts(1, False)), prof(_amounts(2, False))), "benford_change") == []
    )
    assert (
        only(shape.diff(prof(_amounts(1, True)), prof(_amounts(2, False))), "benford_change") == []
    )


_MAD = {"close": 0.003, "acceptable": 0.009, "marginal": 0.0135, "nonconformity": 0.05}
_DIGITS = [0.301, 0.176, 0.125, 0.097, 0.079, 0.067, 0.058, 0.051, 0.046]


def _benford(conformity: str, applicable: bool = True) -> dict[str, Any]:
    return {
        "applicable": applicable,
        "digits": _DIGITS,
        "mad": _MAD[conformity],
        "conformity": conformity,
    }


@pytest.mark.parametrize(
    ("before", "after", "steps"),
    [
        ("close", "acceptable", 1),
        ("close", "marginal", 2),
        ("close", "nonconformity", 3),
        ("acceptable", "nonconformity", 2),
        ("marginal", "nonconformity", 1),
        ("marginal", "close", -2),
    ],
)
def test_benford_class_steps(before: str, after: str, steps: int) -> None:
    b = View("float", 100_000, 0.0, 900, univariate={"benford": _benford(before)})
    c = View("float", 100_000, 0.0, 900, univariate={"benford": _benford(after)})
    out = D.diff_univariate("x", b, c, DEFAULT_THRESHOLDS, True)
    assert [r["kind"] for r in out] == (["benford_change"] if steps >= 2 else [])
    assert [
        r["kind"]
        for r in D.diff_univariate(
            "x", b, c, {**DEFAULT_THRESHOLDS, "benford_class_steps": 1}, True
        )
    ] == (["benford_change"] if steps >= 1 else [])


def test_a_column_where_benford_does_not_apply_has_no_change() -> None:
    b = View("float", 100_000, 0.0, 900, univariate={"benford": _benford("close")})
    c = View(
        "float", 100_000, 0.0, 900, univariate={"benford": {"applicable": False, "reason": "x"}}
    )
    assert D.diff_univariate("x", b, c, DEFAULT_THRESHOLDS, True) == []
    assert D.diff_univariate("x", c, b, DEFAULT_THRESHOLDS, True) == []


def test_a_conformity_drop_inside_the_sampling_noise_is_quiet() -> None:
    # 1,000 values each: the MAD of a sample that size carries a standard error of about 0.003, so
    # "acceptable" to "nonconformity" (0.009 to 0.0152) is two classes on paper and noise in fact
    b = View("float", 1000, 0.0, 900, univariate={"benford": _benford("acceptable")})
    mad_edge = {**_benford("nonconformity"), "mad": 0.0152}
    c = View("float", 1000, 0.0, 900, univariate={"benford": mad_edge})
    assert D.diff_univariate("x", b, c, DEFAULT_THRESHOLDS, True) == []
    far = View("float", 1000, 0.0, 900, univariate={"benford": _benford("nonconformity")})
    close = View("float", 1000, 0.0, 900, univariate={"benford": _benford("close")})
    assert [r["kind"] for r in D.diff_univariate("x", close, far, DEFAULT_THRESHOLDS, True)] == [
        "benford_change"
    ]


def test_two_samples_of_a_distribution_on_a_class_boundary_do_not_drift() -> None:
    # regression: lognormal(3, 0.8) has a MAD of 0.0123 (marginal), so samples of 1,500 land in
    # classes from "acceptable" (seed 6: 0.0091) to "nonconformity" (seed 7: 0.0152)
    def table(seed: int) -> Any:
        return prof([float(v) for v in np.random.default_rng(seed).lognormal(3, 0.8, 1500)])

    a, b = table(6), table(7)
    assert a.to_dict()["columns"]["x"]["benford"]["conformity"] == "acceptable"
    assert b.to_dict()["columns"]["x"]["benford"]["conformity"] == "nonconformity"
    assert only(shape.diff(a, b), "benford_change") == []


def test_the_benford_sample_size_matches_the_profile_cap() -> None:
    from shape.profile.univariate import SAMPLE_CAP

    assert D.BENFORD_SAMPLE_CAP == SAMPLE_CAP


# --- tail_change ---------------------------------------------------------------------------------


def _tail(alpha: float, k: int = 1000) -> dict[str, Any]:
    return {"tail_index": {"alpha": alpha, "k": k, "se": alpha / k**0.5, "heavy": alpha < 2}}


@pytest.mark.parametrize(
    ("before", "after", "reported"),
    [
        (4.0, 1.5, True),
        (4.0, 2.7, True),  # 32.5% down, below 3
        (4.0, 2.9, False),  # 27.5% down is not enough
        (2.9, 2.0, True),  # 31% down
        (2.9, 2.1, False),  # 27.6% down
        (5.0, 3.1, False),  # 38% down but not below 3
        (5.0, 3.0, False),  # not below 3: the bound is strict
        (1.5, 2.5, False),  # a lighter tail
        (2.0, 2.0, False),
    ],
)
def test_tail_change_thresholds(before: float, after: float, reported: bool) -> None:
    b = View("float", 5000, 0.0, 4000, univariate=_tail(before))
    c = View("float", 5000, 0.0, 4000, univariate=_tail(after))
    out = D.diff_univariate("x", b, c, DEFAULT_THRESHOLDS, True)
    assert [r["kind"] for r in out] == (["tail_change"] if reported else [])
    if reported:
        assert out[0]["baseline"] == before and out[0]["current"] == after
        assert out[0]["score"] == pytest.approx(1 - after / before, abs=1e-4)


def test_a_tail_drop_inside_the_estimate_noise_is_quiet() -> None:
    # k = 10: each alpha carries a 30% standard error, so 4.0 -> 2.5 is noise
    b = View("float", 100, 0.0, 90, univariate=_tail(4.0, 10))
    c = View("float", 100, 0.0, 90, univariate=_tail(2.5, 10))
    assert D.diff_univariate("x", b, c, DEFAULT_THRESHOLDS, True) == []


def test_a_pareto_tail_that_got_heavier_is_reported_and_the_same_tail_is_quiet() -> None:
    def pareto(seed: int, alpha: float) -> list[float]:
        return [float(v) for v in (np.random.default_rng(seed).pareto(alpha, N) + 1.0) * 2.0]

    ch = only(shape.diff(prof(pareto(1, 4.0)), prof(pareto(2, 1.5))), "tail_change")
    assert len(ch) == 1 and ch[0]["current"] < 0.7 * ch[0]["baseline"] and ch[0]["current"] < 3
    assert only(shape.diff(prof(pareto(1, 1.5)), prof(pareto(2, 1.5))), "tail_change") == []
    assert only(shape.diff(prof(pareto(1, 1.5)), prof(pareto(2, 4.0))), "tail_change") == []


# --- older profiles, thresholds, scope -----------------------------------------------------------


def test_an_older_profile_diffs_without_the_new_kinds() -> None:
    new_a, new_b = prof(_counts(1)), prof(_counts(2, 0.3))
    old_a = strip(new_a)
    assert "zero_share" not in old_a.to_dict()["columns"]["x"]
    for pair in ((old_a, new_b), (new_a, strip(new_b)), (old_a, strip(new_b))):
        assert not set(kinds(shape.diff(*pair))) & set(KINDS)


def test_the_new_kinds_follow_ignore_only_and_column_thresholds() -> None:
    a = pa.table({"x": _counts(1), "y": _counts(1)})
    b = pa.table({"x": _counts(2, 0.3), "y": _counts(2, 0.3)})
    pa_, pb = shape.profile(a), shape.profile(b)
    cols = sorted(c["column"] for c in only(shape.diff(pa_, pb), "zero_inflation_change"))
    assert cols == ["x", "y"]
    ignored = shape.diff(pa_, pb, ignore_columns=["y"])
    assert [c["column"] for c in only(ignored, "zero_inflation_change")] == ["x"]
    tuned = shape.diff(pa_, pb, column_thresholds={"y": {"zero_share": 0.9}})
    assert only(tuned, "zero_inflation_change")  # inflation appearing is not a share move
    for bad in ({"zero_share": -1}, {"heaping_ratio": "2"}, {"nope": 1}):
        with pytest.raises(ValueError):
            shape.diff(pa_, pb, thresholds=bad)


def test_keys_and_flags_have_no_univariate_changes() -> None:
    ids_a, ids_b = pa.table({"id": np.arange(N)}), pa.table({"id": np.arange(N) * 7})
    assert not set(kinds(shape.diff(shape.profile(ids_a), shape.profile(ids_b)))) & set(KINDS)


def test_a_dataset_names_the_table_and_compare_names_the_field() -> None:
    a = shape.profile({"t": pa.table({"x": _counts(1)})})
    b = shape.profile({"t": pa.table({"x": _counts(2, 0.3)})})
    drifts = [d for d in shape.drift.compare(a, b) if d.kind == "zero_inflation_change"]
    assert [d.path for d in drifts] == ["tables.t.columns.x.zero_inflation"]


def test_no_change_when_the_profiles_are_identical_objects() -> None:
    p = prof(_counts(1, 0.3))
    assert not set(kinds(shape.diff(p, copy.deepcopy(p)))) & set(KINDS)
