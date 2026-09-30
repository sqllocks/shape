"""P1-03: sketches (T-14) in Rust and Python, and the P5/P6/P9 regressions."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from shape.kernel import dispatch, reference
from shape.profile.sketches import KLL, HyperLogLog, SpaceSaving


@pytest.fixture(scope="module")
def native():
    return dispatch._import_native()


def _weight(levels) -> int:
    return sum(len(v) << i for i, v in enumerate(levels))


QS = np.linspace(0.01, 0.99, 50)


def _rank_error(values: np.ndarray, sketch, qs=QS) -> float:
    """Max |rank(estimate(q)) - q| over the grid, as a fraction of n."""
    srt = np.sort(values)
    n = len(srt)
    worst = 0.0
    for q in qs:
        est = sketch.quantile(float(q))
        lo = np.searchsorted(srt, est, side="left") / n
        hi = np.searchsorted(srt, est, side="right") / n
        worst = max(worst, 0.0 if lo <= q <= hi else min(abs(lo - q), abs(hi - q)))
    return worst


# ------------------------------------------------------------------ HyperLogLog


def test_hll_registers_match_exactly_between_rust_and_python(native):
    rng = np.random.default_rng(1)
    for arr in (
        pa.array(rng.integers(0, 10**6, 60_000)),
        pa.array([f"s{v}" for v in rng.integers(0, 10**5, 30_000)] + [None, "x"]),
        pa.array(rng.normal(size=20_000).tolist() + [float("nan"), 1.0, 1]),
    ):
        a, b = native.Hll(14), reference.Hll(14)
        a.update_array(arr)
        b.update_array(arr)
        assert a.registers() == b.registers()
        assert abs(a.estimate() - b.estimate()) <= 1e-9 * b.estimate()


def test_hll_merge_is_exact_associative_and_commutative(native):
    rng = np.random.default_rng(2)
    parts = [pa.array(rng.integers(0, 10**7, 20_000)) for _ in range(3)]
    for impl in (native.Hll, reference.Hll):
        s = [impl(14) for _ in parts]
        for h, p in zip(s, parts, strict=True):
            h.update_array(p)
        whole = impl(14)
        for p in parts:
            whole.update_array(p)
        x = impl(14)
        for h in s:
            x.merge(h)
        y = impl(14)
        for h in reversed(s):
            y.merge(h)
        z = impl(14)  # (a+b)+c vs a+(b+c)
        bc = impl(14)
        bc.merge(s[1])
        bc.merge(s[2])
        z.merge(s[0])
        z.merge(bc)
        assert x.registers() == y.registers() == z.registers() == whole.registers()


def test_hll_batches_equal_one_batch(native):
    data = np.random.default_rng(3).integers(0, 10**6, 100_000)
    one, many = native.Hll(14), native.Hll(14)
    one.update_array(pa.array(data))
    for chunk in np.array_split(data, 37):
        many.update_array(pa.array(chunk))
    assert one.registers() == many.registers()


@pytest.mark.heavy
def test_hll_relative_error_at_the_99th_percentile(native):
    """200 trials of 100k distinct values: error <= 3 x 1.04/sqrt(2^14) at p99."""
    n, errs = 100_000, []
    for t in range(200):
        h = native.Hll(14)
        h.update_array(pa.array(np.arange(n, dtype=np.int64) + t * 10_000_019))
        errs.append(abs(h.estimate() - n) / n)
    bound = 3 * 1.04 / (2**14) ** 0.5
    assert np.percentile(errs, 99) <= bound, (np.percentile(errs, 99), bound)


def test_hll_small_and_large_ranges_are_unbiased(native):
    for n in (0, 1, 10, 1000, 20_000, 500_000):
        h = native.Hll(14)
        if n:
            h.update_array(pa.array(np.arange(n, dtype=np.int64)))
        est = h.estimate()
        assert abs(est - n) <= max(0.5, 0.04 * n), (n, est)


def test_hll_state_round_trip_and_validation(native):
    h = native.Hll(12)
    h.update_array(pa.array(range(5000)))
    back = native.Hll.from_registers(12, h.registers())
    assert back.registers() == h.registers() and back.estimate() == h.estimate()
    with pytest.raises(ValueError):
        native.Hll(3)
    with pytest.raises(ValueError):
        native.Hll.from_registers(12, b"\0\0")
    with pytest.raises(ValueError):
        a = native.Hll(10)
        a.merge(native.Hll(11))


# -------------------------------------------------------------------------- KLL


def test_kll_levels_and_quantiles_match_between_rust_and_python(native):
    rng = np.random.default_rng(4)
    data = np.concatenate([rng.normal(size=30_000), rng.exponential(size=10_000)])
    a, b = native.Kll(200), reference.Kll(200)
    a.update_values(pa.array(data))
    b.update_values(pa.array(data))
    assert a.levels() == b.levels() and a.n == b.n == len(data)
    for q in (0, 0.01, 0.5, 0.9, 0.999, 1):
        assert a.quantile(q) == b.quantile(q)
    other_a, other_b = native.Kll(200), reference.Kll(200)
    extra = pa.array(rng.normal(size=7_777))
    other_a.update_values(extra)
    other_b.update_values(extra)
    a.merge(other_a)
    b.merge(other_b)
    assert a.levels() == b.levels() and a.n == b.n


def test_p9_total_weight_is_preserved_exactly():
    """Compacting an odd-sized level used to drop weight."""
    for k in (8, 50, 200):
        sk = KLL(k)
        for i in range(5000 + k):
            sk.update(float(i % 313))
            assert _weight(sk.levels) == sk.n
        other = KLL(k)
        for i in range(1234):
            other.update(float(i))
        sk.merge(other)
        assert _weight(sk.levels) == sk.n == 5000 + k + 1234


@pytest.mark.heavy
def test_kll_rank_error_is_at_most_one_percent(native):
    rng = np.random.default_rng(5)
    n = 1_000_000
    for name, data in {
        "normal": rng.normal(size=n),
        "lognormal": rng.lognormal(size=n),
        "sorted": np.arange(n, dtype=np.float64),
        "reversed": np.arange(n, dtype=np.float64)[::-1].copy(),
        "few_values": rng.integers(0, 50, n).astype(np.float64),
    }.items():
        sk = native.Kll(200)
        sk.update_values(pa.array(data))
        assert _rank_error(data, sk) <= 0.01, name


def test_kll_nulls_and_nan_are_excluded(native):
    sk = native.Kll(200)
    sk.update_values(pa.array([1.0, None, float("nan"), 3.0]))
    assert sk.n == 2 and sk.quantile(0.0) == 1.0 and sk.quantile(1.0) == 3.0
    assert native.Kll(200).quantile(0.5) is None


@settings(max_examples=40, deadline=None)
@given(
    st.lists(st.integers(1000, 4000), min_size=1, max_size=8),
    st.randoms(use_true_random=False),
)
def test_kll_merge_order_does_not_matter_for_weight_or_error(sizes, rnd):
    native = dispatch._import_native()
    rng = np.random.default_rng(sum(sizes))
    parts = [rng.normal(size=n) for n in sizes]
    sketches = []
    for p in parts:
        s = native.Kll(200)
        s.update_values(pa.array(p))
        sketches.append(s)
    order = list(range(len(sketches)))
    rnd.shuffle(order)
    acc = native.Kll(200)
    for i in order:
        acc.merge(sketches[i])
    total = np.concatenate(parts)
    assert acc.n == len(total) == _weight(acc.levels())
    assert _rank_error(total, acc) <= 0.015


# ------------------------------------------------------------------ SpaceSaving


def _zipf_keys(n, seed, a=1.3, universe=5000):
    rng = np.random.default_rng(seed)
    return (rng.zipf(a, n) % universe).astype(np.uint64)


def _check_bounds(top, truth: dict[int, int], n: int, capacity: int):
    for key, count, err in top:
        t = truth.get(key, 0)
        assert count - err <= t <= count, (key, count, err, t)
        assert err <= n / capacity + 1e-9


def test_space_saving_rust_equals_python_and_respects_bounds(native):
    keys = _zipf_keys(120_000, 6)
    a, b = native.SpaceSaving(64), reference.SpaceSaving(64)
    a.update_keys(pa.array(keys))
    b.update_keys(pa.array(keys))
    assert a.top() == b.top() and a.n == b.n == len(keys)
    truth = dict(zip(*np.unique(keys, return_counts=True), strict=True))
    _check_bounds(a.top(), {int(k): int(v) for k, v in truth.items()}, len(keys), 64)
    heavy = sorted(truth.items(), key=lambda kv: -kv[1])[:5]
    assert {int(k) for k, _ in heavy} <= {k for k, _, _ in a.top()}


def test_p6_merge_is_symmetric_and_keeps_error_terms(native):
    k1, k2 = _zipf_keys(50_000, 7), _zipf_keys(60_000, 8, a=1.15)
    for impl in (native.SpaceSaving, reference.SpaceSaving):
        a, b, a2, b2 = impl(64), impl(64), impl(64), impl(64)
        for s, k in ((a, k1), (a2, k1), (b, k2), (b2, k2)):
            s.update_keys(pa.array(k))
        a.merge(b)
        b2.merge(a2)
        assert a.top() == b2.top() and a.n == b2.n == len(k1) + len(k2)
        allkeys = np.concatenate([k1, k2])
        truth = {
            int(k): int(v) for k, v in zip(*np.unique(allkeys, return_counts=True), strict=True)
        }
        _check_bounds(a.top(), truth, a.n, 64)
        assert any(e > 0 for _, _, e in a.top())  # error terms are not dropped
    x, y = native.SpaceSaving(64), reference.SpaceSaving(64)
    for s in (x, y):
        s.update_keys(pa.array(k1))
    ox, oy = native.SpaceSaving(64), reference.SpaceSaving(64)
    for s in (ox, oy):
        s.update_keys(pa.array(k2))
    x.merge(ox)
    y.merge(oy)
    assert x.top() == y.top()


def test_space_saving_batches_equal_one_batch_within_bounds(native):
    keys = _zipf_keys(200_000, 9)
    one, many = native.SpaceSaving(64), native.SpaceSaving(64)
    one.update_keys(pa.array(keys))
    for chunk in np.array_split(keys, 20):
        many.update_keys(pa.array(chunk))
    assert one.top() == many.top()  # a stream is a stream: chunking cannot matter
    parts = []
    for chunk in np.array_split(keys, 8):
        s = native.SpaceSaving(64)
        s.update_keys(pa.array(chunk))
        parts.append(s)
    acc = native.SpaceSaving(64)
    for s in parts:
        acc.merge(s)
    truth = {int(k): int(v) for k, v in zip(*np.unique(keys, return_counts=True), strict=True)}
    _check_bounds(acc.top(), truth, len(keys), 64)


@pytest.mark.heavy
def test_p5_space_saving_holds_at_most_capacity_after_ten_million_updates(native):
    s = native.SpaceSaving(64)
    rng = np.random.default_rng(10)
    for _ in range(10):
        s.update_keys(pa.array(rng.integers(0, 2**63, 1_000_000).astype(np.uint64)))
    assert len(s) <= 64 and s.n == 10_000_000 and len(s.top()) == 64


def test_p5_python_space_saving_does_not_grow():
    """The heap was never pruned: 200k updates left 200k entries."""
    s = SpaceSaving(64)
    for i in range(200_000):
        s.update(i)
    assert len(s.counts) == 64 and len(s._seq) == 64


def test_space_saving_python_api_still_works():
    s = SpaceSaving(4)
    for x in ["a"] * 20 + ["b"] * 10 + ["c"] * 3:
        s.update(x)
    assert s.top(1)[0][0] == "a" and s.n == 33 and s.state()["entries"][0][:2] == ["a", 20]


def test_space_saving_hashes_arrow_values(native):
    a = native.SpaceSaving(8)
    a.update_array(pa.array(["x", "y", "x", None, "x", float("nan")][:5]))
    assert a.n == 4 and a.top()[0][1] == 3
    b = reference.SpaceSaving(8)
    b.update_array(pa.array(["x", "y", "x", None, "x"]))
    assert a.top() == b.top()


def test_python_hll_state_and_estimate():
    h = HyperLogLog(14)
    for i in range(50_000):
        h.update(i)
    assert abs(h.estimate() - 50_000) / 50_000 < 0.03
    assert h.state()["estimator"] == "ertl-improved" and isinstance(h.state()["registers"], str)
