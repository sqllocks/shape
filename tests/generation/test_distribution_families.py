"""P4-05: histograms, mixtures and truncation, and the new families through the ``distribution``
strategy."""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from shape.builtins.distributions.families import FamilyError, family_by_name
from shape.generation.engine import Engine
from shape.generation.rng import RowStream

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmarks" / "vs_spindle"
sys.path.insert(0, str(BENCH / "strategy_1to1"))
import cases as cases_mod  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "vs_spindle_schema_import", BENCH / "schema_import.py"
)
assert _spec and _spec.loader
schema_import = importlib.util.module_from_spec(_spec)
sys.modules["vs_spindle_schema_import"] = schema_import
_spec.loader.exec_module(schema_import)


def _tvd(a: np.ndarray[Any, Any], b: np.ndarray[Any, Any]) -> float:
    return 0.5 * float(np.abs(a / a.sum() - b / b.sum()).sum())


def test_empirical_histogram_draws_match_the_source():
    rng = np.random.default_rng(11)
    source = np.concatenate([rng.gamma(2.0, 3.0, 150_000), rng.normal(40, 2, 50_000)])
    family = family_by_name("histogram")
    params = family.fit(source, bins=80)
    draws = family.sample(RowStream(4, "t", "h", "v"), 0, 1_000_000, params)
    edges = np.asarray(params["edges"])
    src_counts, _ = np.histogram(source, bins=edges)
    got_counts, _ = np.histogram(draws, bins=edges)
    assert _tvd(src_counts.astype(float), got_counts.astype(float)) <= 0.01
    assert draws.min() >= edges[0] and draws.max() <= edges[-1]


def test_histogram_with_empty_bins_and_chunks():
    params = {"edges": [0.0, 1.0, 2.0, 5.0], "weights": [3.0, 0.0, 1.0]}
    family = family_by_name("histogram")
    s = RowStream(1, "t", "h", "v")
    whole = family.sample(s, 0, 20_000, params)
    assert not ((whole >= 1.0) & (whole < 2.0)).any()
    parts = np.concatenate([family.sample(s, a, 5_000, params) for a in range(0, 20_000, 5_000)])
    assert (whole == parts).all()
    for bad in (
        {"edges": [0.0], "weights": []},
        {"edges": [0.0, 1.0, 1.0], "weights": [1.0, 1.0]},
        {"edges": [0.0, 1.0], "weights": [0.0]},
        {"edges": [0.0, 1.0, 2.0], "weights": [1.0]},
    ):
        with pytest.raises(FamilyError):
            family.sample(s, 0, 5, bad)


def test_mixture_proportions_and_moments():
    comps = [
        {"weight": 0.6, "distribution": "normal", "params": {"mu": 0, "sigma": 1}},
        {"weight": 0.4, "distribution": "gamma", "params": {"k": 9, "theta": 1}},
    ]
    family = family_by_name("mixture")
    x = family.sample(RowStream(2, "t", "m", "v"), 0, 800_000, {"components": comps})
    # P(Gamma(9, 1) < 4) = P(Poisson(4) >= 9)
    gamma_below = 1.0 - sum(math.exp(-4) * 4**k / math.factorial(k) for k in range(9))
    assert abs((x < 4.0).mean() - (0.6 * 0.99997 + 0.4 * gamma_below)) < 0.004
    assert abs(x.mean() - (0.6 * 0 + 0.4 * 9)) < 0.02
    s = RowStream(3, "t", "m", "v")
    whole = family.sample(s, 0, 9_000, {"components": comps})
    parts = np.concatenate(
        [family.sample(s, a, 1_500, {"components": comps}) for a in range(0, 9_000, 1_500)]
    )
    assert (whole == parts).all()
    with pytest.raises(FamilyError):
        family.sample(s, 0, 5, {"components": []})
    with pytest.raises(FamilyError):
        family.sample(s, 0, 5, {"components": [{"weight": 0}]})


def test_truncation_is_the_conditional_distribution():
    family = family_by_name("truncated")
    params = {"base": "gamma", "base_params": {"k": 2, "theta": 3}, "low": 2.0, "high": 9.0}
    x = family.sample(RowStream(8, "t", "x", "v"), 0, 400_000, params)
    assert x.min() >= 2.0 and x.max() <= 9.0
    base = family_by_name("gamma").sample(
        RowStream(9, "t", "y", "v"), 0, 4_000_000, params["base_params"]
    )
    ref = base[(base >= 2.0) & (base <= 9.0)]
    grid = np.linspace(2.0, 9.0, 200)
    ks = np.abs(
        np.searchsorted(np.sort(x), grid) / len(x) - np.searchsorted(np.sort(ref), grid) / len(ref)
    )
    assert ks.max() < 0.006
    half = family.sample(RowStream(8, "t", "x", "v"), 0, 1_000, {**params, "high": None})
    assert half.min() >= 2.0
    s = RowStream(3, "t", "tr", "v")
    whole = family.sample(s, 0, 8_000, params)
    parts = np.concatenate([family.sample(s, a, 2_000, params) for a in range(0, 8_000, 2_000)])
    assert (whole == parts).all()


def test_truncation_into_an_empty_region_fails_clearly():
    family = family_by_name("truncated")
    s = RowStream(1, "t", "x", "v")
    with pytest.raises(FamilyError, match="almost none|too little"):
        family.sample(
            s, 0, 1000, {"base": "normal", "base_params": {"mu": 0, "sigma": 1}, "low": 40.0}
        )
    with pytest.raises(FamilyError, match="low < high"):
        family.sample(s, 0, 5, {"base": "normal", "base_params": {}, "low": 3.0, "high": 3.0})
    with pytest.raises(FamilyError, match="unknown distribution"):
        family.sample(s, 0, 5, {"base": "nope", "base_params": {}})


def _column(generator: dict[str, Any], rows: int = 30_000, seed: int = 5) -> np.ndarray[Any, Any]:
    case = {
        "strategy": "distribution",
        "helpers": {},
        "regex": None,
        "column": cases_mod._col("x", "decimal", {"strategy": "distribution", **generator}),
    }
    raw = cases_mod.schema_for(case, rows=rows)
    table = Engine(schema_import.import_dump(raw), seed=seed).generate_table("t")
    return np.asarray(table.column("x").to_pylist())


def test_new_families_work_through_the_distribution_strategy():
    g = _column({"distribution": "gamma", "k": 2, "theta": 3})
    assert abs(g.mean() - 6) < 0.15
    e = _column({"distribution": "exponential", "lambda": 0.5})
    assert abs(e.mean() - 2) < 0.06
    nb = _column({"distribution": "negative_binomial", "r": 5, "p": 0.4})
    assert abs(nb.mean() - 7.5) < 0.15
    m = _column(
        {
            "distribution": "mixture",
            "components": [
                {"weight": 0.5, "distribution": "normal", "mean": -5, "std_dev": 1},
                {"weight": 0.5, "distribution": "normal", "mean": 5, "std_dev": 1},
            ],
        }
    )
    assert abs((m > 0).mean() - 0.5) < 0.01
    t = _column(
        {
            "distribution": "truncated",
            "base": "normal",
            "base_params": {"mu": 0, "sigma": 1},
            "min": -1,
            "max": 2,
        }
    )
    assert t.min() >= -1 and t.max() <= 2
    h = _column({"distribution": "histogram", "edges": [0, 10, 20], "weights": [1, 3]})
    assert abs((h >= 10).mean() - 0.75) < 0.01


def test_a_plugin_distribution_can_be_named():
    x = _column({"distribution": "lognormal", "params": {"s": 0.5, "loc": 0.0, "scale": 20.0}})
    assert x.min() > 0 and 15 < np.median(x) < 25
