"""``derived`` ``add_days``: the day offsets come from one native pass and the date arithmetic works
from the arrays' buffers; the results are those of the step-by-step code they replaced (reproduced
here as the oracle)."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pytest

from shape.builtins.strategies import derived as D
from shape.generation.strategy_kit import stream
from shape.plugins.api.v1 import GenerationContext


def _ctx(n: int, start: int = 0) -> GenerationContext:
    return GenerationContext(seed=9, table="t", column="c", chunk=0, row_start=start, n_rows=n)


def _oracle_days(ctx, params):
    """``sample_days`` as it was before the kernel fused it."""
    dist = params.get("distribution", "uniform")
    low, high = float(params.get("min", 1)), float(params.get("max", 30))
    s = stream(ctx, "days")
    if dist == "log_normal":
        mean, sigma = float(params.get("mean", 2.0)), float(params.get("sigma", 0.8))
        days = np.clip(np.exp(mean + sigma * s.normal(ctx.row_start, ctx.n_rows)), low, high)
    elif dist == "normal":
        mean, std = float(params.get("mean", 10.0)), float(params.get("std_dev", 3.0))
        days = np.clip(mean + std * s.normal(ctx.row_start, ctx.n_rows), low, high)
    else:
        days = low + (high - low) * s.uniform(ctx.row_start, ctx.n_rows)
    return np.rint(days).astype(np.int64)


PARAMS = [
    {},
    {"distribution": "uniform", "min": 3, "max": 30},
    {"distribution": "uniform", "min": 7, "max": 7},
    {"min": 365, "max": 365},
    {"distribution": "uniform", "min": 0, "max": 0},
    {"distribution": "uniform", "min": -5, "max": -5},
    {"distribution": "log_normal"},
    {"distribution": "log_normal", "mean": 2.0, "sigma": 0.8, "min": 1, "max": 90},
    {"distribution": "log_normal", "mean": 6.0, "sigma": 1.5, "min": 0, "max": 400},
    {"distribution": "log_normal", "mean": 1.0, "sigma": 0.1, "min": 50, "max": 5},  # low > high
    {"distribution": "normal", "mean": 10.0, "std_dev": 3.0, "min": 1, "max": 30},
]


@pytest.mark.parametrize("params", PARAMS, ids=lambda p: str(p)[:60])
@pytest.mark.parametrize("n", [0, 1, 500, 40_000])
def test_sample_days_equal_the_numpy_steps(params, n):
    ctx = _ctx(n, start=13)
    assert np.array_equal(D.sample_days(ctx, params), _oracle_days(ctx, params))


def _oracle_add_days(values: pa.Array, days: np.ndarray) -> pa.Array:
    t = values.type
    if pa.types.is_date32(t):
        unit_per_day, base = 1, pc.cast(values, pa.int32()).cast(pa.int64())
    else:
        unit_per_day = D._SECONDS_PER_DAY * D._TIMESTAMP_UNITS[t.unit]
        base = pc.cast(values, pa.int64())
    mask = np.asarray(base.is_null().to_numpy(zero_copy_only=False)) if base.null_count else None
    filled = np.asarray(pc.fill_null(base, 0).to_numpy(zero_copy_only=False))
    shifted = filled + days * unit_per_day
    out = pa.array(shifted, mask=mask)
    return out.cast(pa.int32()).cast(t) if pa.types.is_date32(t) else out.cast(t)


def _sources(n: int):
    rng = np.random.default_rng(n)
    micros = rng.integers(-(2**40), 2**50, n)
    yield pa.array(micros, type=pa.timestamp("us"))
    yield pa.array(micros * 1000, type=pa.timestamp("ns"))
    yield pa.array(micros // 1000, type=pa.timestamp("ms"))
    yield pa.array(micros // 10**6, type=pa.timestamp("s"))
    yield pa.array(micros, type=pa.timestamp("us", tz="UTC"))
    yield pa.array(rng.integers(-20_000, 40_000, n).astype(np.int32), type=pa.date32())
    with_null = [None if i % 7 == 3 else int(v) for i, v in enumerate(micros)]
    yield pa.array(with_null, type=pa.timestamp("us"))


@pytest.mark.parametrize("n", [1, 300, 5000])
def test_add_days_equals_the_cast_chain(n):
    days = np.random.default_rng(1).integers(-400, 400, n).astype(np.int64)
    for values in _sources(n):
        got = D._add_days(values, days)
        want = _oracle_add_days(values, days)
        assert got.type == want.type
        assert got.equals(want), values.type
    sliced = next(iter(_sources(n + 10))).slice(3, n)
    assert D._add_days(sliced, days).equals(_oracle_add_days(sliced, days))


def test_a_date_that_leaves_the_int32_range_still_raises_as_before():
    values = pa.array([2**31 - 2], pa.int32()).cast(pa.date32())
    with pytest.raises(pa.ArrowInvalid):
        D._add_days(values, np.array([10], dtype=np.int64))


def test_chunking_does_not_change_the_derived_days():
    params = {"distribution": "log_normal", "mean": 2.0, "sigma": 0.8, "min": 1, "max": 90}
    whole = D.sample_days(_ctx(300, 0), params)
    parts = np.concatenate([D.sample_days(_ctx(100, s), params) for s in (0, 100, 200)])
    assert np.array_equal(whole, parts)
