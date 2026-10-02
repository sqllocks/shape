"""``temporal`` builds ``timestamp[us]`` and ``timestamp[ns]`` straight from int64 buffers; the
result is what the checked pyarrow cast gave, and a range whose nanoseconds do not fit still
fails the way the cast did."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest

from shape.builtins.strategies import temporal as T
from shape.generation.strategy_kit import StrategyError
from shape.plugins.api.v1 import GenerationContext


def _ctx(n: int = 400, start: int = 0) -> GenerationContext:
    return GenerationContext(seed=11, table="t", column="c", chunk=0, row_start=start, n_rows=n)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2015-01-01", "2025-12-31"),
        ("1970-01-01", "1970-01-02"),
        ("1969-06-01", "1971-06-01"),
        ("1700-01-01", "2250-01-01"),  # close to both limits of nanoseconds
        ("2022-03-04T05:06:07.123456", "2022-03-04T05:06:07.123999"),
    ],
)
def test_nanoseconds_equal_the_checked_cast(start: str, end: str) -> None:
    spec = {"pattern": "uniform", "start": start, "end": end}
    us = T.Temporal().generate(spec, _ctx())
    ns = T.Temporal().generate({**spec, "unit": "ns"}, _ctx())
    assert us.type == pa.timestamp("us") and ns.type == pa.timestamp("ns")
    assert ns.equals(us.cast(pa.timestamp("ns")))
    assert ns.cast(pa.timestamp("us")).equals(us)


def test_a_range_beyond_the_nanosecond_limit_is_still_an_error() -> None:
    spec = {"pattern": "uniform", "start": "1500-01-01", "end": "1600-01-01", "unit": "ns"}
    with pytest.raises(StrategyError, match="do not fit unit"):
        T.Temporal().generate(spec, _ctx())


def test_the_limit_is_where_the_cast_stops() -> None:
    """Values just inside and just outside what an int64 of nanoseconds can hold."""
    limit = T._NS_LIMIT
    for micros in (limit, -limit, limit + 1, -limit - 1):
        values = pa.array([0, micros, 5], type=pa.timestamp("us"))
        fast = T._as_nanoseconds(values)
        try:
            want = values.cast(pa.timestamp("ns"))
        except pa.ArrowInvalid:
            assert fast is None, micros
        else:
            assert fast is not None and fast.equals(want), micros


def test_nulls_empty_and_sliced_arrays_take_the_pyarrow_route_or_equal_it() -> None:
    with_null = pa.array([1, None, 3], type=pa.timestamp("us"))
    assert T._as_nanoseconds(with_null) is None
    assert T._as_nanoseconds(pa.array([], type=pa.timestamp("us"))) is None
    whole = pa.array(np.arange(100, dtype=np.int64) * 1_000_003, type=pa.timestamp("us"))
    sliced = whole.slice(7, 40)
    fast = T._as_nanoseconds(sliced)
    assert fast is not None and fast.equals(sliced.cast(pa.timestamp("ns")))


def test_uniform_timestamps_do_not_depend_on_chunking() -> None:
    spec = {"pattern": "uniform", "start": "2020-01-01", "end": "2024-01-01", "unit": "ns"}
    whole = T.Temporal().generate(spec, _ctx(300, 0))
    parts = [T.Temporal().generate(spec, _ctx(100, s)) for s in (0, 100, 200)]
    assert pa.concat_arrays(parts).equals(whole)
    assert len(T.Temporal().generate(spec, _ctx(0, 0))) == 0


@pytest.mark.parametrize("unit", ["us", "ns"])
@pytest.mark.parametrize("n", [0, 1, 777, 40_000])
@pytest.mark.parametrize(
    "spec",
    [
        {"start": "2015-01-01", "end": "2025-12-31"},
        {"start": "1970-01-01", "end": "1970-01-01T00:00:01"},
        {"range": {"start": "2020-02-29", "end": "2020-03-01"}},
        {"start": "1700-01-01", "end": "2250-01-01"},
        {"start": "1500-01-01", "end": "2500-01-01", "pattern": "uniform"},  # beyond nanoseconds
    ],
)
def test_the_fused_uniform_column_equals_the_stepwise_one(spec, n, unit, monkeypatch):
    spec = {"pattern": "uniform", **spec, "unit": unit}
    ctx = _ctx(n, start=5)
    try:
        fused = T.Temporal().generate(spec, ctx)
    except StrategyError:
        fused = "error"
    with monkeypatch.context() as m:
        m.setattr(T.Temporal, "_uniform_fused", staticmethod(lambda *a, **k: None))
        try:
            plain = T.Temporal().generate(spec, ctx)
        except StrategyError:
            plain = "error"
    if isinstance(plain, str) or isinstance(fused, str):
        assert fused == plain == "error"  # a range that does not fit is an error either way
    else:
        assert fused.type == plain.type == pa.timestamp(unit)
        assert fused.equals(plain)


def test_a_range_that_ends_before_it_starts_is_an_error() -> None:
    with pytest.raises(StrategyError, match="must end after"):
        T.Temporal().generate({"start": "2020-01-02", "end": "2020-01-01"}, _ctx())
