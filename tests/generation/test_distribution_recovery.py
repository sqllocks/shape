"""P4-05: every family's draws are fitted back to the parameters that made them.

The parameter table below is fixed (plan section 7, P4-05). For each family, 10**6 draws from the
row-addressed stream are fitted and every parameter must come back within
max(2 %, 3 standard errors). The standard error is the spread of the fit over 20 consecutive
blocks of the draws divided by sqrt(20): conservative for estimators that converge faster than
1/sqrt(n) (the edges of a triangular distribution).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from shape.builtins.distributions.families import FAMILIES, family_by_name
from shape.generation.rng import RowStream

N = 1_000_000
BLOCKS = 20

# family -> parameters (the plan's table; names are the family's own)
TABLE: dict[str, dict[str, Any]] = {
    "normal": {"mu": 10.0, "sigma": 2.0},
    "log_normal": {"mu": 3.0, "sigma": 0.5},
    "pareto": {"alpha": 1.5, "xm": 1.0},
    "zipf": {"a": 2.0, "max": 1_000_000.0},
    "geometric": {"p": 0.2},
    "poisson": {"lam": 4.0},
    "bernoulli": {"p": 0.3},
    "exponential": {"lam": 0.5},
    "gamma": {"k": 2.0, "theta": 3.0},
    "beta": {"a": 2.0, "b": 5.0},
    "weibull": {"k": 1.5, "lam": 2.0},
    "triangular": {"low": 0.0, "mode": 3.0, "high": 10.0},
    "negative_binomial": {"r": 5.0, "p": 0.4},
    "power_law_cutoff": {"alpha": 2.0, "lam": 0.01, "xmin": 1.0},
}
MIXTURE = {
    "components": [
        {"weight": 0.6, "distribution": "normal", "params": {"mu": 0.0, "sigma": 1.0}},
        {"weight": 0.4, "distribution": "normal", "params": {"mu": 8.0, "sigma": 1.0}},
    ]
}


def _flat(params: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for i, c in enumerate(params.get("components", ())):
        out[f"w{i}"] = c["weight"]
        out.update({f"{k}{i}": v for k, v in c["params"].items()})
    out.update({k: float(v) for k, v in params.items() if k != "components"})
    return out


def _check(name: str, params: dict[str, Any]) -> None:
    family = family_by_name(name)
    x = family.sample(RowStream(20260930, "t", name, "v"), 0, N, params)
    assert x.shape == (N,) and np.isfinite(x).all()
    got = _flat(family.fit(x))
    truth = _flat(params)
    blocks = [_flat(family.fit(b)) for b in np.array_split(x, BLOCKS)]
    for key, want in truth.items():
        if key in ("max", "xmin"):  # support bounds: fixed by the draws, not estimated
            continue
        se = float(np.std([b[key] for b in blocks], ddof=1)) / math.sqrt(BLOCKS)
        tol = max(0.02 * abs(want), 3.0 * se)
        assert abs(got[key] - want) <= tol, f"{name}.{key}: {got[key]} vs {want} (tol {tol})"


@pytest.mark.parametrize("name", sorted(TABLE))
def test_parameters_are_recovered(name):
    _check(name, TABLE[name])


def test_mixture_parameters_are_recovered():
    _check("mixture", MIXTURE)


def test_the_table_covers_every_family_of_the_plan():
    plan = {
        "normal", "log_normal", "pareto", "zipf", "geometric", "poisson", "bernoulli",
        "exponential", "gamma", "beta", "weibull", "triangular", "negative_binomial",
        "power_law_cutoff",
    }  # fmt: skip
    assert plan == set(TABLE)
    assert {*TABLE, "mixture", "uniform", "histogram", "truncated"} == set(FAMILIES)


def test_the_check_can_fail():
    """A fit that is 5 % off is rejected by the same rule."""
    family = family_by_name("gamma")
    x = family.sample(RowStream(1, "t", "g", "v"), 0, 200_000, TABLE["gamma"])
    fitted = family.fit(x)
    assert abs(fitted["k"] - 2.0) <= 0.02 * 2.0
    assert abs(1.05 * fitted["k"] - 2.0) > 0.02 * 2.0


@pytest.mark.parametrize("name", sorted(TABLE))
def test_draws_do_not_depend_on_the_chunking(name):
    family = family_by_name(name)
    params = TABLE[name]
    s = RowStream(5, "t", name, "v")
    whole = family.sample(s, 0, 30_000, params)
    parts = np.concatenate(
        [family.sample(s, a, min(7_001, 30_000 - a), params) for a in range(0, 30_000, 7_001)]
    )
    assert (whole == parts).all()
    assert not (family.sample(RowStream(6, "t", name, "v"), 0, 30_000, params) == whole).all()


def test_support_and_moments():
    s = RowStream(3, "t", "m", "v")

    def draw(name: str, **p: Any) -> np.ndarray[Any, Any]:
        return family_by_name(name).sample(s, 0, 400_000, p)

    g = draw("gamma", k=0.4, theta=2.0)  # shape below one uses the boost
    assert g.min() > 0 and abs(g.mean() - 0.8) < 0.01
    b = draw("beta", a=0.5, b=0.5)
    assert 0 < b.min() and b.max() < 1 and abs(b.mean() - 0.5) < 0.005
    t = draw("triangular", low=2.0, mode=2.0, high=6.0)
    assert t.min() >= 2.0 and t.max() <= 6.0 and abs(t.mean() - 10 / 3) < 0.01
    nb = draw("negative_binomial", r=2.5, p=0.3)
    assert nb.min() >= 0 and abs(nb.mean() - 2.5 * 0.7 / 0.3) < 0.05
    w = draw("weibull", k=0.7, lam=3.0)
    assert w.min() > 0
    e = draw("exponential", lam=4.0)
    assert abs(e.mean() - 0.25) < 0.002
    p = draw("power_law_cutoff", alpha=1.2, lam=0.1, xmin=2.0)
    assert p.min() >= 2.0 and np.isfinite(p).all()


@pytest.mark.parametrize(
    ("name", "params"),
    [
        ("exponential", {"lam": 0}),
        ("gamma", {"k": -1}),
        ("beta", {"a": 0}),
        ("weibull", {"k": 0}),
        ("triangular", {"low": 5, "mode": 1, "high": 9}),
        ("negative_binomial", {"r": 1, "p": 1.0}),
        ("power_law_cutoff", {"lam": 0}),
    ],
)
def test_bad_parameters_raise(name, params):
    with pytest.raises(ValueError):
        family_by_name(name).sample(RowStream(1, "t", "c", "v"), 0, 10, params)
