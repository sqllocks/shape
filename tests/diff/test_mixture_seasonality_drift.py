"""W7-03 item 4: the diff kinds ``mixture_change`` and ``seasonality_change``."""

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

KINDS = ("mixture_change", "seasonality_change")
FIELDS = ("mixture", "seasonality")
N = 4000


def kinds(d: Any) -> list[str]:
    return [c["kind"] for c in d.changes]


def only(d: Any, kind: str) -> list[dict[str, Any]]:
    return [c for c in d.changes if c["kind"] == kind]


def mix(seed: int, w: float = 0.3, gap: float = 4.0, n: int = N) -> pa.Table:
    rng = np.random.default_rng(seed)
    x = np.where(rng.random(n) < w, rng.normal(0.0, 1.0, n), rng.normal(gap, 1.0, n))
    return pa.table({"x": pa.array(x)})


def mixture_of(t: pa.Table) -> dict[str, Any]:
    return dict(shape.profile(t).to_dict()["columns"]["x"]["mixture"])


# --- registration and documentation --------------------------------------------------------------


def test_kinds_and_thresholds_are_registered_and_documented() -> None:
    assert KIND_SEVERITY["mixture_change"] == "low"
    assert KIND_SEVERITY["seasonality_change"] == "medium"
    assert DEFAULT_THRESHOLDS["mixture_weight"] == 0.1
    assert DEFAULT_THRESHOLDS["seasonality_strength"] == 0.2
    text = (Path(__file__).parents[2] / "docs" / "DRIFT.md").read_text(encoding="utf-8")
    for key in (*KINDS, "mixture_weight", "seasonality_strength"):
        assert f"`{key}`" in text, key


def test_the_thresholds_are_settable_like_the_others() -> None:
    a, b = shape.profile(mix(1, 0.3)), shape.profile(mix(2, 0.45))  # weight moved by 0.15
    assert len(only(shape.diff(a, b), "mixture_change")) == 1
    assert only(shape.diff(a, b, thresholds={"mixture_weight": 0.2}), "mixture_change") == []
    with pytest.raises(ValueError, match="unknown thresholds"):
        shape.diff(a, b, thresholds={"mixture_weigth": 0.2})


# --- mixture_change ------------------------------------------------------------------------------


def test_a_second_population_appearing_is_a_k_change() -> None:
    rng = np.random.default_rng(1)
    one = pa.table({"x": pa.array(rng.normal(0.0, 1.0, N))})
    ch = only(shape.diff(shape.profile(one), shape.profile(mix(2, 0.4))), "mixture_change")
    assert len(ch) == 1 and ch[0]["baseline"] == {"k": 1} and ch[0]["current"] == {"k": 2}
    assert ch[0]["severity"] == "low" and 0.0 < ch[0]["score"] <= 1.0


def test_a_component_weight_move_over_0_1_is_reported_and_one_under_is_not() -> None:
    a = shape.profile(mix(1, 0.3))
    assert len(only(shape.diff(a, shape.profile(mix(2, 0.45))), "mixture_change")) == 1
    assert only(shape.diff(a, shape.profile(mix(3, 0.36))), "mixture_change") == []


def test_two_samples_of_one_mixture_are_quiet() -> None:
    for seed in range(5):
        d = shape.diff(shape.profile(mix(seed)), shape.profile(mix(seed + 100)))
        assert only(d, "mixture_change") == [], seed


def test_two_samples_of_skewed_unimodal_data_are_quiet() -> None:
    """The k of a log-normal fit is unstable; the evidence guard keeps it quiet."""
    for seed in range(6):
        ta = pa.table({"x": pa.array(np.random.default_rng(seed).lognormal(3.0, 0.8, N))})
        tb = pa.table({"x": pa.array(np.random.default_rng(seed + 50).lognormal(3.0, 0.8, N))})
        assert only(shape.diff(shape.profile(ta), shape.profile(tb)), "mixture_change") == [], seed


def test_a_multimodal_flip_without_a_distribution_difference_is_quiet() -> None:
    comp = lambda w: {"weight": w, "mean": 0.0, "sd": 1.0}  # noqa: E731
    b = {"k": 2, "components": [comp(0.5), comp(0.5)], "multimodal": True}
    c = {"k": 2, "components": [comp(0.97), comp(0.03)], "multimodal": False}
    vb = View("float", 4000, 0.0, 4000, univariate={"mixture": b})
    vc = View("float", 4000, 0.0, 4000, univariate={"mixture": c})
    assert D.diff_univariate("x", vb, vc, DEFAULT_THRESHOLDS, True) == []  # same distribution


def test_too_few_rows_and_a_missing_side_report_nothing() -> None:
    a, b = shape.profile(mix(1, 0.3)), shape.profile(mix(2, 0.5))
    assert only(shape.diff(a, b, thresholds={"min_rows": 100_000}), "mixture_change") == []
    old = copy.deepcopy(a.to_dict())
    del old["columns"]["x"]["mixture"]
    older = type(a)(old, name=a.name)
    assert only(shape.diff(older, b), "mixture_change") == []
    assert only(shape.diff(b, older), "mixture_change") == []


# --- seasonality_change --------------------------------------------------------------------------


def daily(cycle: float, seed: int, weeks: int = 30, noise: float = 1.5) -> pa.Table:
    rng = np.random.default_rng(seed)
    n = weeks * 7
    figure = np.array([0.0, 1.0, 2.0, 3.0, 2.0, -3.0, -5.0])
    days = np.datetime64("2023-01-02") + np.arange(n).astype("timedelta64[D]")
    v = 100.0 + cycle * np.tile(figure, weeks) + rng.normal(0.0, noise, n)
    return pa.table({"day": pa.array(days.astype("datetime64[s]")), "sales": pa.array(v)})


def test_removing_the_weekly_cycle_is_a_seasonality_change() -> None:
    with_cycle, without = shape.profile(daily(2.0, 1)), shape.profile(daily(0.0, 2))
    assert with_cycle.to_dict()["columns"]["sales"]["seasonality"]["seasonal"] is True
    assert without.to_dict()["columns"]["sales"]["seasonality"]["seasonal"] is False
    ch = only(shape.diff(with_cycle, without), "seasonality_change")
    assert len(ch) == 1 and ch[0]["column"] == "sales" and ch[0]["severity"] == "medium"
    assert ch[0]["baseline"]["period"] == 7 and ch[0]["baseline"]["strength"] > 0.6
    assert ch[0]["current"]["strength"] < 0.6


def test_the_same_cycle_twice_is_quiet() -> None:
    d = shape.diff(shape.profile(daily(2.0, 1)), shape.profile(daily(2.0, 2)))
    assert only(d, "seasonality_change") == []


def _seasonal(period: int, strength: float, seasonal: bool, tc: str = "day") -> dict[str, Any]:
    return {
        "applicable": True,
        "time_column": tc,
        "granularity": "day",
        "period": period,
        "strength": strength,
        "acf": 0.5,
        "seasonal": seasonal,
    }


def _views(b: dict[str, Any], c: dict[str, Any]) -> tuple[View, View]:
    return (
        View("float", 500, 0.0, 400, univariate={"seasonality": b}),
        View("float", 500, 0.0, 400, univariate={"seasonality": c}),
    )


def _go(b: dict[str, Any], c: dict[str, Any], th: dict[str, Any] | None = None) -> list[str]:
    vb, vc = _views(b, c)
    out = D.diff_univariate("x", vb, vc, {**DEFAULT_THRESHOLDS, **(th or {})}, True)
    return [r["kind"] for r in out if r["kind"] == "seasonality_change"]


def test_the_period_change_and_the_strength_boundary() -> None:
    assert _go(_seasonal(7, 0.8, True), _seasonal(12, 0.8, True)) == ["seasonality_change"]
    # a period change between series that are not seasonal is noise
    assert _go(_seasonal(7, 0.3, False), _seasonal(52, 0.3, False)) == []
    assert _go(_seasonal(7, 0.8, True), _seasonal(7, 0.55, False)) == ["seasonality_change"]
    # strength moves of exactly the threshold are not a change, just over it are
    assert _go(_seasonal(7, 0.9, True), _seasonal(7, 0.7, True)) == []
    assert _go(_seasonal(7, 0.95, True), _seasonal(7, 0.7, True)) == ["seasonality_change"]
    assert _go(_seasonal(7, 0.9, True), _seasonal(7, 0.7, True), {"seasonality_strength": 0.1}) == [
        "seasonality_change"
    ]


def test_a_flag_that_flips_at_the_edge_without_moving_is_quiet() -> None:
    assert _go(_seasonal(7, 0.61, True), _seasonal(7, 0.59, False)) == []
    assert _go(_seasonal(7, 0.65, True), _seasonal(7, 0.5, False)) == ["seasonality_change"]


def test_an_unmeasured_side_or_another_time_column_reports_nothing() -> None:
    na = {"applicable": False, "time_column": "day", "reason": "constant series"}
    assert _go(_seasonal(7, 0.9, True), na) == []
    assert _go(na, _seasonal(7, 0.9, True)) == []
    assert _go(_seasonal(7, 0.9, True), _seasonal(7, 0.1, False, "other")) == []


def test_a_profile_without_the_fields_reports_neither_kind() -> None:
    p = shape.profile(daily(2.0, 1))
    old = copy.deepcopy(p.to_dict())
    for f in FIELDS:
        old["columns"]["sales"].pop(f, None)
    older = type(p)(old, name=p.name)
    d = shape.diff(older, shape.profile(daily(0.0, 2)))
    assert not set(KINDS) & set(kinds(d))
    assert not set(KINDS) & set(kinds(shape.diff(shape.profile(daily(0.0, 2)), older)))
