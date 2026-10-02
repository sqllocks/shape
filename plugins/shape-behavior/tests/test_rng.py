"""The counter-based draws are uniform, independent across keys and a pure function of the key."""

import numpy as np
from shape_behavior import rng


def test_same_key_same_value_and_order_free():
    ids = np.arange(1000, dtype=np.int64)
    a = rng.uniform(5, ids, 3, 0)
    b = rng.uniform(5, ids[::-1], 3, 0)[::-1]
    assert np.array_equal(a, b)
    assert np.array_equal(a[10:20], rng.uniform(5, ids[10:20], 3, 0))


def test_uniform_moments_and_bins():
    u = rng.uniform(1, np.arange(400_000, dtype=np.int64), 0, 0)
    assert u.min() >= 0 and u.max() < 1
    assert abs(u.mean() - 0.5) < 0.003
    assert abs(u.var() - 1 / 12) < 0.001
    hist = np.bincount((u * 20).astype(int), minlength=20)
    chi2 = float(((hist - 20_000) ** 2 / 20_000).sum())
    assert chi2 < 50  # 19 degrees of freedom; 50 is far past p = 0.0001


def test_streams_seeds_and_draw_indexes_are_uncorrelated():
    ids = np.arange(100_000, dtype=np.int64)
    base = rng.uniform(1, ids, 0, 0)
    for other in (rng.uniform(2, ids, 0, 0), rng.uniform(1, ids, 1, 0), rng.uniform(1, ids, 0, 1)):
        assert abs(np.corrcoef(base, other)[0, 1]) < 0.02
    shifted = rng.uniform(1, ids + 1, 0, 0)
    assert abs(np.corrcoef(base[:-1], shifted[:-1])[0, 1]) < 0.02


def test_name_key_is_stable():
    assert rng.name_key("gender") == rng.name_key("gender") != rng.name_key("sbp")
    assert rng.name_key("gender") < 2**40
