"""``shape.privacy.dp``: noise from OS entropy unless seeded, the reference's formulas, clipping."""

from __future__ import annotations

import hashlib

import numpy as np
import pyarrow as pa
import pytest

from shape.privacy.dp import DifferentialPrivacy, DPResult


def _table(n=200):
    rng = np.random.default_rng(0)
    return pa.table(
        {
            "i": pa.array(rng.integers(0, 100, n)),
            "f": pa.array(rng.normal(50, 10, n), mask=rng.random(n) < 0.1),
            "const": pa.array([5.0] * n),
            "flag": pa.array(rng.random(n) < 0.5),
            "s": pa.array([f"s{k}" for k in range(n)]),
        }
    )


def test_a_thousand_calls_without_a_seed_never_repeat_the_noise():
    t, dp = _table(30), DifferentialPrivacy(epsilon=1.0)
    seen = set()
    for _ in range(1000):
        out, _ = dp.apply(t)
        digest = hashlib.sha256(
            out["i"].to_numpy(zero_copy_only=False).tobytes()
            + out["f"].fill_null(0.0).to_numpy(zero_copy_only=False).tobytes()
        ).hexdigest()
        seen.add(digest)
    assert len(seen) == 1000


def test_the_same_seed_reproduces_and_another_seed_does_not():
    t, dp = _table(), DifferentialPrivacy()
    a, _ = dp.apply(t, seed=5)
    b, _ = dp.apply(t, seed=5)
    c, _ = dp.apply(t, seed=6)
    assert a.equals(b) and not a.equals(c)


def test_a_seed_reproduces_the_reference_formula():
    """Same draws, in column order, one per row: Laplace(0, range/eps), clipped to the range."""
    t = _table()
    out, res = DifferentialPrivacy(epsilon=0.5).apply(t, seed=7)
    rng = np.random.default_rng(7)
    for name in ("i", "f"):
        v = t[name].cast(pa.float64()).fill_null(np.nan).to_numpy(zero_copy_only=False)
        lo, hi = np.nanmin(v), np.nanmax(v)
        noise = rng.laplace(0, (hi - lo) / 0.5, size=len(v))
        want = np.clip(v + noise, lo, hi)
        got = out[name].fill_null(np.nan).to_numpy(zero_copy_only=False)
        np.testing.assert_array_equal(got, want)
        assert res.actual_sensitivity[name] == hi - lo
    assert res.columns_noised == ["i", "f"] and res.epsilon == 0.5 and res.mechanism == "laplace"


def test_gaussian_sigma_and_an_explicit_generator():
    t = _table()
    out, res = DifferentialPrivacy(epsilon=2.0, delta=1e-6, mechanism="gaussian").apply(
        t, rng=np.random.default_rng(3)
    )
    v = t["i"].to_numpy()
    span = float(v.max() - v.min())
    sigma = span * np.sqrt(2 * np.log(1.25 / 1e-6)) / 2.0
    want = np.clip(v + np.random.default_rng(3).normal(0, sigma, size=len(v)), v.min(), v.max())
    np.testing.assert_array_equal(out["i"].to_numpy(), want)
    assert res.mechanism == "gaussian"


def test_what_is_noised_what_is_not_and_nulls_stay_null():
    t = _table()
    out, res = DifferentialPrivacy().apply(t, seed=1)
    assert res.columns_noised == ["i", "f"]  # constant, boolean and text columns are not noised
    assert (
        out["const"].equals(t["const"])
        and out["flag"].equals(t["flag"])
        and out["s"].equals(t["s"])
    )
    assert out["i"].type == pa.float64()  # integers become floats
    assert out["f"].null_count == t["f"].null_count
    assert out.column_names == t.column_names and out.num_rows == t.num_rows


def test_clipping_can_be_switched_off():
    t = _table()
    lo, hi = float(t["i"].to_numpy().min()), float(t["i"].to_numpy().max())
    clipped, _ = DifferentialPrivacy(epsilon=0.1).apply(t, seed=2)
    assert clipped["i"].to_numpy().min() >= lo and clipped["i"].to_numpy().max() <= hi
    free, _ = DifferentialPrivacy(epsilon=0.1, clip_to_range=False).apply(t, seed=2)
    assert free["i"].to_numpy().min() < lo or free["i"].to_numpy().max() > hi


def test_arguments_are_checked():
    with pytest.raises(ValueError, match="mechanism"):
        DifferentialPrivacy(mechanism="uniform")
    with pytest.raises(ValueError, match="epsilon"):
        DifferentialPrivacy(epsilon=0)
    with pytest.raises(ValueError, match="delta"):
        DifferentialPrivacy(mechanism="gaussian", delta=1.5)
    with pytest.raises(ValueError, match="not both"):
        DifferentialPrivacy().apply(_table(), seed=1, rng=np.random.default_rng(1))


def test_the_result_is_json_ready_and_the_empty_table_is_returned_unchanged():
    t = pa.table({"x": pa.array([], type=pa.float64())})
    out, res = DifferentialPrivacy().apply(t, seed=0)
    assert out.equals(t) and res.columns_noised == [] and isinstance(res, DPResult)
    assert res.to_dict() == {
        "epsilon": 1.0,
        "mechanism": "laplace",
        "columns_noised": [],
        "actual_sensitivity": {},
    }


def test_no_fixed_default_seed_in_the_source():
    import inspect

    from shape.privacy import dp

    src = inspect.getsource(dp)
    assert "default_rng(0)" not in src and "seed=0" not in src
