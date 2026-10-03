"""W7-03 item 1: Gaussian mixtures by BIC (``shape.profile.mixture``)."""

from __future__ import annotations

import numpy as np
import pyarrow as pa

import shape
from shape.profile import mixture as M


def _two_populations(n: int = 10_000, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    pick = rng.random(n) < 0.3
    return np.where(pick, rng.normal(0.0, 1.0, n), rng.normal(4.0, 1.0, n))


def test_two_components_four_sd_apart_give_k2_and_the_planted_weights() -> None:
    m = M.mixture(_two_populations())
    assert m is not None
    assert m["k"] == 2
    assert m["multimodal"] is True
    low, high = m["components"]
    assert abs(low["weight"] - 0.3) < 0.03 and abs(high["weight"] - 0.7) < 0.03
    assert abs(low["mean"] - 0.0) < 0.2 and abs(high["mean"] - 4.0) < 0.2
    assert set(m["bic_by_k"]) == {"1", "2", "3", "4"}
    assert m["bic_by_k"]["2"] == min(m["bic_by_k"].values())


def test_a_single_normal_gives_k1_on_several_seeds() -> None:
    for seed in range(5):
        x = np.random.default_rng(seed).normal(50.0, 7.0, 10_000)
        m = M.mixture(x)
        assert m is not None and m["k"] == 1 and m["multimodal"] is False, seed
        assert abs(m["components"][0]["mean"] - 50.0) < 0.5
        assert abs(m["components"][0]["sd"] - 7.0) < 0.5


def test_the_result_is_deterministic() -> None:
    x = _two_populations()
    assert M.mixture(x) == M.mixture(x.copy())


def test_a_minor_component_is_not_multimodal() -> None:
    rng = np.random.default_rng(3)
    n = 20_000
    x = np.where(rng.random(n) < 0.02, rng.normal(10.0, 1.0, n), rng.normal(0.0, 1.0, n))
    m = M.mixture(x)
    assert m is not None and m["k"] >= 2
    assert min(c["weight"] for c in m["components"]) < M.MIN_WEIGHT
    assert m["multimodal"] is False


def test_the_minimum_sample_is_200_finite_values() -> None:
    rng = np.random.default_rng(1)
    x = rng.normal(0.0, 1.0, 300)
    assert M.mixture(x[:199]) is None
    assert M.mixture(x[:200]) is not None
    padded = np.concatenate([x[:199], [np.nan, np.inf, -np.inf]])
    assert M.mixture(padded) is None  # non-finite values do not count
    assert M.mixture(np.concatenate([x[:200], [np.nan]])) is not None


def test_a_constant_column_has_no_mixture() -> None:
    assert M.mixture(np.full(500, 3.0)) is None


def test_a_large_column_is_read_through_a_sample_of_at_most_the_cap() -> None:
    x = _two_populations(200_000, seed=11)
    m = M.mixture(x)
    assert m is not None and m["k"] == 2
    assert M.SAMPLE_CAP <= 100_000


def test_bic_of_a_dominant_family_is_finite_for_heavy_ties() -> None:
    x = np.repeat(np.arange(5.0), 100)  # five spikes: the spread floor keeps EM finite
    m = M.mixture(x)
    assert m is not None
    assert all(np.isfinite(v) for v in m["bic_by_k"].values())
    assert all(c["sd"] > 0 for c in m["components"])


def test_the_profile_carries_the_mixture_and_leaves_the_distribution_fields_alone() -> None:
    x = _two_populations(3000)
    p = shape.profile(pa.table({"v": pa.array(x)}))
    col = p.to_dict()["columns"]["v"]
    assert col["mixture"]["k"] == 2
    assert col["distribution_candidates"] and "distribution" in col
    assert (
        "mixture" not in shape.profile(pa.table({"v": pa.array(x[:150])})).to_dict()["columns"]["v"]
    )


def test_describe_prints_k_and_components() -> None:
    m = M.mixture(_two_populations())
    lines = M.describe({"mixture": m})
    assert len(lines) == 1 and "k=2" in lines[0] and "multimodal" in lines[0]
    assert M.describe({}) == []
