"""P1-15: the exact-mode column kernels in Rust against their numpy/pyarrow reference twins."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pytest

from shape.kernel import dispatch, reference


@pytest.fixture(scope="module")
def native():
    return dispatch._import_native()


def _arr(x):
    return x if isinstance(x, pa.Array) else pa.array(x)


def _same_counts(native, values, top_n=500, row_count=None, want_sorted=True, want_uniq=True):
    row_count = len(values) if row_count is None else row_count
    got = native.count_numeric(pa.array(values), top_n, row_count, want_sorted, want_uniq)
    want = reference.count_numeric(pa.array(values), top_n, row_count, want_sorted, want_uniq)
    assert got["cardinality"] == want["cardinality"]
    assert got["all_whole"] == want["all_whole"]
    for key in ("keys", "counts", "sorted", "uniq"):
        assert _arr(got[key]).equals(_arr(want[key])), key


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("n", [1, 7, 300, 20_000, 120_000])
def test_count_numeric_matches_reference_with_ties(native, seed, n):
    rng = np.random.default_rng(seed)
    card = [3, 50, 700, n][seed % 4]
    ints = rng.integers(-card, card, n).astype(np.int64)
    _same_counts(native, ints)
    floats = rng.integers(-card, card, n) / 4.0
    _same_counts(native, floats)
    _same_counts(native, floats, top_n=10, row_count=n * 10)  # not an enum: only top_n keys


def test_count_numeric_infinities_and_whole_numbers(native):
    v = np.array([1.0, np.inf, -np.inf, 2.0, 2.0, np.inf, -9.223372036854775808e18, 1e300])
    _same_counts(native, v)
    _same_counts(native, np.array([0.5, 1.0, 2.0]))
    _same_counts(native, np.array([2.0**63, 1.0]))  # numpy: 2**63 does not survive astype(int64)


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 17, 1000, 200_001])
def test_numeric_stats_is_bitwise_identical(native, n):
    rng = np.random.default_rng(n)
    for values in (
        rng.normal(5.0, 3.0, n),
        rng.lognormal(0, 2, n),
        rng.integers(0, 4, n).astype(np.float64),
        np.full(n, 3.25),
    ):
        got = native.numeric_stats(pa.array(values), None)
        want = reference.numeric_stats(pa.array(values), None)
        assert got["has_quantiles"] == want["has_quantiles"]
        assert got["outliers"] == want["outliers"]
        for k in ("mean", "std"):
            assert np.float64(got[k]).tobytes() == np.float64(want[k]).tobytes(), k
        if want["quantiles"] is not None:
            assert [np.float64(q).tobytes() for q in got["quantiles"]] == [
                np.float64(q).tobytes() for q in want["quantiles"]
            ]
        # a pre-sorted copy gives the same answer
        pre = native.numeric_stats(pa.array(values), pa.array(np.sort(values)))
        assert pre["outliers"] == got["outliers"]


def test_numeric_stats_with_infinities(native):
    values = np.array([1.0, 2.0, 3.0, 4.0, 5.0, np.inf, -np.inf, 7.0])
    got = native.numeric_stats(pa.array(values), None)
    want = reference.numeric_stats(pa.array(values), None)
    assert got["outliers"] == want["outliers"]
    assert repr(got["quantiles"]) == repr(want["quantiles"])


@pytest.mark.parametrize("large", [False, True])
def test_value_counts_str_matches_arrow(native, large):
    rng = np.random.default_rng(3)
    words = np.array([f"w{i}" for i in range(400)] + ["", "é", "日本"])
    for n, k in ((50, 5), (5000, 400), (250_000, 200_000)):
        pool = (
            words[rng.integers(0, len(words), n)] if k < 1000 else rng.integers(0, k, n).astype(str)
        )
        arr = pa.array(pool.tolist(), pa.large_string() if large else pa.string())
        u, c = native.value_counts_str(arr)
        u, c = _arr(u), _arr(c)
        want_u, want_c = reference.value_counts_str(arr)
        assert u.type == arr.type
        assert u.equals(want_u)
        assert c.equals(want_c)


def test_top_indices_is_a_stable_descending_argsort(native):
    rng = np.random.default_rng(9)
    for n, need in ((10, 3), (1000, 500), (1000, 5000), (5, 0), (200_000, 500)):
        counts = pa.array(rng.integers(1, 6, n).astype(np.int64))
        assert _arr(native.top_indices(counts, need)).equals(reference.top_indices(counts, need))


@pytest.mark.parametrize("unit", ["s", "ms", "us", "ns"])
def test_temporal_counts_match_arrow(native, unit):
    rng = np.random.default_rng(5)
    secs = rng.integers(-3_000_000_000, 4_000_000_000, 150_000)  # 1875 .. 2096
    ts = pa.array(secs * {"s": 1, "ms": 10**3, "us": 10**6, "ns": 10**9}[unit]).cast(
        pa.timestamp(unit)
    )
    assert pc.min_max(ts)["min"].as_py() is not None
    assert native.temporal_counts(ts) == reference.temporal_counts(ts)
    one = ts.slice(0, 1)
    assert native.temporal_counts(one) == reference.temporal_counts(one)


def test_product_profile_uses_the_native_kernels_and_matches_the_reference(tmp_path, monkeypatch):
    """The same table profiled with the native kernel and the reference kernel gives the same
    document, byte for byte (T-22 against the numpy twin)."""
    import json

    import shape

    rng = np.random.default_rng(0)
    n = 30_000
    table = pa.table(
        {
            "id": np.arange(n),
            "x": rng.normal(size=n),
            "y": rng.lognormal(size=n),
            "k": rng.integers(0, 40, n),
            "s": [f"v{i % 700}" for i in range(n)],
            "t": pa.array(rng.integers(1_500_000_000, 1_700_000_000, n)).cast(pa.timestamp("s")),
        }
    )
    docs = []
    for mode in ("rust", "python"):
        monkeypatch.setenv("SHAPE_KERNEL", mode)
        dispatch.reset()
        docs.append(json.dumps(shape.profile(table).to_dict(), sort_keys=False))
    dispatch.reset()
    assert docs[0] == docs[1]


# --- P1-17: per-value text and rounding work -------------------------------------------------


def _fuzz_floats(seed: int, n: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    parts = [
        rng.normal(0, 1, n) * 10.0 ** rng.integers(-30, 30, n),
        rng.integers(-1000, 1000, n) / rng.choice([1, 2, 4, 8, 10, 1000, 1_000_000], n),
        rng.random(n),
        np.frombuffer(rng.bytes(8 * n), dtype=np.uint64).view(np.float64),  # any bit pattern
        np.array([0.0, -0.0, 1e16, 1e15, 9.999999999999999e15, 1e-4, 1e-5, 0.5e-6, 2.5e-6, 1.5e-6]),
        np.array([np.inf, -np.inf, np.nan, 5e-324, 1.7976931348623157e308, 123456789012345680.0]),
    ]
    return np.concatenate(parts)


@pytest.mark.parametrize("seed", range(4))
def test_float_repr_is_pythons_repr(native, seed):
    vals = pa.array(_fuzz_floats(seed, 20_000))
    got = _arr(native.float_repr(vals))
    assert got.equals(reference.float_repr(vals))
    assert got.to_pylist() == [str(v) for v in vals.to_pylist()]


@pytest.mark.parametrize("seed", range(4))
def test_round6_is_pythons_round(native, seed):
    vals = pa.array(_fuzz_floats(seed, 20_000))
    got = _arr(native.round6(vals)).to_numpy(zero_copy_only=False)
    want = reference.round6(vals).to_numpy(zero_copy_only=False)
    assert np.array_equal(got.view(np.uint64), want.view(np.uint64))  # bitwise, NaN and -0.0 too


def test_round6_ties_and_half_values(native):
    vals = pa.array([0.0000005, 0.0000015, 0.0000025, 2.5e-7, -2.5e-7, 0.1234565, 1234567.8912345])
    got = _arr(native.round6(vals)).to_pylist()
    assert got == [round(v, 6) for v in vals.to_pylist()]


@pytest.mark.parametrize("seed", range(3))
def test_date_iso_matches_arrow_cast(native, seed):
    rng = np.random.default_rng(seed)
    days = rng.integers(-719_162, 2_932_896, 50_000).astype(np.int32)  # 0001-01-01 .. 9999-12-31
    mask = rng.random(len(days)) < 0.1
    vals = pa.array(days, pa.date32(), mask=mask)
    assert _arr(native.date_iso(vals)).equals(reference.date_iso(vals))


def test_date_iso_declines_years_outside_four_digits(native):
    assert native.date_iso(pa.array([3_000_000, 0], pa.date32())) is None
