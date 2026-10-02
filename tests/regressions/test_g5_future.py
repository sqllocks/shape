"""G5: ``generation/future.py``: ``missingness`` never applied the mask, and ``gaussian_copula``
was a plain multivariate normal with no marginal transforms (and an eigenvalue clip that left
variances different from 1). Both are now real, and the profile path uses them.

Every test here fails on the code before the fix (``missingness`` returned the input unchanged;
``gaussian_copula`` took no marginals)."""

from __future__ import annotations

import numpy as np
import pyarrow as pa

import shape
from shape.generation.future import gaussian_copula, missingness


def test_missingness_nulls_the_values_it_masks() -> None:
    values, mask = missingness(np.arange(20_000, dtype=np.float64), 0.2, seed=1)
    assert isinstance(values, pa.Array)
    assert values.null_count == int(mask.sum())
    assert 0.18 < mask.mean() < 0.22
    kept = np.flatnonzero(~mask)
    assert values.take(pa.array(kept)).to_pylist() == [float(i) for i in kept]


def test_missingness_keeps_the_type_and_is_seeded() -> None:
    ints = pa.array(np.arange(1000, dtype=np.int64))
    a, _ = missingness(ints, 0.5, seed=3)
    b, _ = missingness(ints, 0.5, seed=3)
    c, _ = missingness(ints, 0.5, seed=4)
    assert a.type == pa.int64() and a.equals(b) and not a.equals(c)
    assert missingness(ints, 0.0)[0].null_count == 0
    assert missingness(ints, 1.0)[0].null_count == 1000


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    rx, ry = np.argsort(np.argsort(x)), np.argsort(np.argsort(y))
    return float(np.corrcoef(rx, ry)[0, 1])


def test_copula_gives_each_column_its_own_marginal() -> None:
    corr = [[1.0, 0.7], [0.7, 1.0]]
    uniform = lambda u: 10.0 + 10.0 * u  # noqa: E731  uniform on [10, 20)
    expo = lambda u: -np.log1p(-u) / 0.5  # noqa: E731  exponential, rate 0.5
    out = gaussian_copula(corr, 100_000, seed=2, marginals=[uniform, expo])
    assert out[:, 0].min() >= 10 and out[:, 0].max() < 20
    assert abs(out[:, 0].mean() - 15) < 0.1  # uniform marginal
    assert abs(out[:, 1].mean() - 2.0) < 0.05  # exponential marginal
    assert (out[:, 1] >= 0).all()
    assert abs(_spearman(out[:, 0], out[:, 1]) - (6 / np.pi) * np.arcsin(0.7 / 2)) < 0.01


def test_copula_without_marginals_has_unit_variance_even_for_a_bad_matrix() -> None:
    """A matrix that is not a correlation matrix is repaired to one: unit variances, in range."""
    bad = [[1.0, 0.9, 0.9], [0.9, 1.0, -0.9], [0.9, -0.9, 1.0]]
    out = gaussian_copula(bad, 200_000, seed=5)
    assert np.allclose(out.std(axis=0), 1.0, atol=0.02)
    assert np.abs(np.corrcoef(out.T)).max() <= 1.0 + 1e-9


def test_generating_from_a_profile_applies_both() -> None:
    """The product path: nulls appear at the profile's rate and the correlation survives them."""
    rng = np.random.default_rng(7)
    n = 40_000
    z = rng.multivariate_normal([0, 0], [[1, 0.7], [0.7, 1]], n)
    x = np.round(np.exp(2 + 0.8 * z[:, 0]), 2)
    y = np.round(50 + 10 * z[:, 1], 2)
    table = pa.table(
        {
            "id": np.arange(1, n + 1),
            "x": x,
            "y": pa.array(y, mask=rng.random(n) < 0.15),
        }
    )
    profile = shape.profile(table, name="t")
    original = profile.to_dict()
    out = shape.generate(profile, seed=11).tables["t"]
    assert abs(out["y"].null_count / n - original["columns"]["y"]["null_rate"]) < 0.01
    again = shape.profile(out, name="t").to_dict()
    want = original["correlation_matrix"]["x"]["y"]
    assert abs(again["correlation_matrix"]["x"]["y"] - want) < 0.03
