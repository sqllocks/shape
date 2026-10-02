"""P4-03: the generation kernel (Philox, alias sampling, strings, temporal) against its twin.

The native kernel and ``shape.kernel.reference.gen`` must agree: bit for bit on every integer and
string result, and to a few ulp where a transcendental function is involved. Philox itself is
checked against numpy's own ``Philox`` (the T-16 oracle).
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pyarrow as pa
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from shape.kernel import dispatch
from shape.kernel.reference import gen as ref

M64 = (1 << 64) - 1


@pytest.fixture(scope="module")
def nat():
    return dispatch._import_native()


def _np(a):
    return np.asarray(pa.array(a).to_numpy(zero_copy_only=False))


def _same(a, b):
    assert pa.array(a).type == pa.array(b).type
    assert pa.array(a).equals(pa.array(b))


KEYS = [(0, 0), (1, 2), (M64, M64), (0x0123456789ABCDEF, 0xFEDCBA9876543210)]


# ------------------------------------------------------------------ Philox known answers


@pytest.mark.parametrize("k0,k1", KEYS)
def test_philox_matches_numpy_known_answers(nat, k0, k1):
    key = k0 | (k1 << 64)
    oracle = np.random.Philox(key=key, counter=0).random_raw(4096)
    assert (_np(nat.philox_words(k0, k1, 0, 4096)) == oracle).all()
    for block in (1, 7, 1000, 2**20 + 3):
        want = np.random.Philox(key=key, counter=block).random_raw(8)
        assert (_np(nat.philox_words(k0, k1, 4 * block, 8)) == want).all()


def test_philox_unaligned_rows_and_wide_rows(nat):
    k0, k1 = KEYS[3]
    flat = _np(nat.philox_words(k0, k1, 0, 400))
    for per_row in (1, 2, 3, 5, 7):
        for start in (0, 1, 3, 4, 13):
            n = 40
            got = _np(nat.philox_words(k0, k1, start, n, per_row))
            assert (got == flat[start * per_row : (start + n) * per_row]).all()


@pytest.mark.parametrize("k0,k1", KEYS)
def test_reference_equals_native_for_words(nat, k0, k1):
    for start, n, per_row in [(0, 0, 1), (0, 1, 1), (5, 300, 3), (1_000_003, 777, 2)]:
        _same(
            nat.philox_words(k0, k1, start, n, per_row), ref.philox_words(k0, k1, start, n, per_row)
        )


def test_native_is_independent_of_chunk_layout_and_threads(nat):
    k0, k1 = KEYS[1]
    n = 200_000  # large enough to run on several threads
    whole = _np(nat.philox_uniform(k0, k1, 0, n))
    cuts = [0, 1, 4095, 8192, 65_537, 150_000, n]
    parts = [
        _np(nat.philox_uniform(k0, k1, a, b - a)) for a, b in zip(cuts, cuts[1:], strict=False)
    ]
    assert (np.concatenate(parts) == whole).all()
    nat.set_threads(0)
    wide = _np(nat.philox_words(k0, k1, 3, 400_000, 3))
    one = np.concatenate(
        [_np(nat.philox_words(k0, k1, 3 + a, 1000, 3)) for a in range(0, 400_000, 1000)]
    )
    assert (wide == one).all()


def test_uniform_and_normal_agree_with_reference(nat):
    k0, k1 = KEYS[3]
    for per_row, slot in [(1, 0), (3, 2), (4, 1)]:
        _same(
            nat.philox_uniform(k0, k1, 17, 5000, per_row, slot),
            ref.philox_uniform(k0, k1, 17, 5000, per_row, slot),
        )
    for per_row, slot in [(2, 0), (5, 3)]:
        a = _np(nat.philox_normal(k0, k1, 9, 5000, per_row, slot))
        b = _np(ref.philox_normal(k0, k1, 9, 5000, per_row, slot))
        np.testing.assert_allclose(a, b, rtol=1e-13, atol=1e-13)
    u = _np(nat.philox_uniform(k0, k1, 0, 400_000))
    assert 0.0 <= u.min() and u.max() < 1.0 and abs(u.mean() - 0.5) < 0.005
    z = _np(nat.philox_normal(k0, k1, 0, 400_000))
    assert abs(z.mean()) < 0.01 and abs(z.std() - 1.0) < 0.01


@pytest.mark.parametrize("module", ["nat", "ref"])
def test_bad_slots_and_sizes_raise(nat, module):
    m = nat if module == "nat" else ref
    with pytest.raises(ValueError):
        m.philox_words(1, 2, 0, 5, 0)
    with pytest.raises(ValueError):
        m.philox_uniform(1, 2, 0, 5, 2, 2)
    with pytest.raises(ValueError):
        m.philox_normal(1, 2, 0, 5, 2, 1)


# ------------------------------------------------------------------ alias sampling


def test_alias_build_is_identical(nat):
    rng = np.random.default_rng(3)
    for n in (1, 2, 5, 37, 1000):
        w = rng.random(n) ** 3
        w[rng.integers(0, n)] = 0.0
        if w.sum() == 0:
            w[0] = 1.0
        a = nat.alias_build(pa.array(w))
        b = ref.alias_build(pa.array(w))
        _same(a[0], b[0])
        _same(a[1], b[1])


@given(st.lists(st.floats(0, 1e6, allow_nan=False, allow_infinity=False), min_size=1, max_size=40))
@settings(max_examples=60, deadline=None)
def test_alias_build_property(weights):
    nat_ = dispatch._import_native()
    if sum(weights) <= 0:
        with pytest.raises(ValueError):
            nat_.alias_build(pa.array(weights))
        with pytest.raises(ValueError):
            ref.alias_build(pa.array(weights))
        return
    p, a = nat_.alias_build(pa.array(weights))
    rp, ra = ref.alias_build(pa.array(weights))
    _same(p, rp)
    _same(a, ra)
    # the table conserves probability mass
    prob, alias, n = _np(p), _np(a), len(weights)
    mass = np.zeros(n)
    for i in range(n):
        mass[i] += prob[i] / n
        mass[alias[i]] += (1 - prob[i]) / n
    np.testing.assert_allclose(mass, np.array(weights) / sum(weights), atol=1e-9)


@pytest.mark.parametrize("n_cat", [1, 3, 50, 5000])
def test_alias_sample_agrees_and_matches_the_weights(nat, n_cat):
    rng = np.random.default_rng(n_cat)
    w = rng.random(n_cat) + 0.01
    prob, alias = nat.alias_build(pa.array(w))
    k0, k1 = KEYS[1]
    got = nat.alias_sample(prob, alias, k0, k1, 11, 3000, 3, 1)
    _same(got, ref.alias_sample(prob, alias, k0, k1, 11, 3000, 3, 1))
    big = _np(nat.alias_sample(prob, alias, k0, k1, 0, 400_000))
    freq = np.bincount(big, minlength=n_cat) / len(big)
    tvd = 0.5 * np.abs(freq - w / w.sum()).sum()
    assert tvd < 0.02 + 0.5 * np.sqrt(n_cat / len(big))


def test_alias_sample_rejects_bad_tables(nat):
    for m in (nat, ref):
        with pytest.raises(ValueError):
            m.alias_build(pa.array([]))
        with pytest.raises(ValueError):
            m.alias_build(pa.array([0.0, 0.0]))
        with pytest.raises(ValueError):
            m.alias_build(pa.array([1.0, float("nan")]))
        with pytest.raises(ValueError):
            m.alias_build(pa.array([1, 2]))  # not float64
        with pytest.raises(ValueError):
            m.alias_sample(pa.array([1.0]), pa.array([5], pa.int64()), 1, 2, 0, 3)


# ------------------------------------------------------------------ strings


POOL = ["alpha", "Beta", "gamma delta", "", "ÉCOLE", "straße", "o'neil-smith", None, "ΟΔΥΣΣΕΥΣ"]


@pytest.mark.parametrize("pa_type", [pa.string(), pa.large_string()])
def test_pool_take(nat, pa_type):
    pool = pa.array(POOL, type=pa_type)
    idx = pa.array([0, 3, 8, 7, 2, 2, None, 1], type=pa.int64())
    got = nat.pool_take(pool, idx)
    _same(got, ref.pool_take(pool, idx))
    assert got.to_pylist() == [POOL[i] if i is not None else None for i in idx.to_pylist()]
    for m in (nat, ref):
        with pytest.raises(ValueError):
            m.pool_take(pool, pa.array([len(POOL)], type=pa.int64()))
        with pytest.raises(ValueError):
            m.pool_take(pool, pa.array([-1], type=pa.int64()))
        with pytest.raises(ValueError):
            m.pool_take(pa.array([1, 2], pa.int64()), pa.array([0], pa.int64()))


def test_template_strings(nat):
    names = pa.array(["ann", None, "bob", "é"], type=pa.string())
    big = pa.array(["x", "y", "z", "w"], type=pa.large_string())
    ids = pa.array([7, -42, None, 123456], type=pa.int64())
    cols = [names, ids, big]
    lits = ["INV-", "/", "-", ":end"]
    slots = [(1, 6), (0, 0), (2, 0)]
    got = nat.template_strings(lits, slots, cols, 4)
    _same(got, ref.template_strings(lits, slots, cols, 4))
    assert got.to_pylist() == ["INV-000007/ann-x:end", None, None, "INV-123456/é-w:end"]
    # zero width, repeated and unused columns, no slots at all
    for lits2, slots2 in [(["", ""], [(1, 0)]), (["a", "b", "c"], [(2, 3), (2, 0)]), (["lit"], [])]:
        _same(
            nat.template_strings(lits2, slots2, cols, 4),
            ref.template_strings(lits2, slots2, cols, 4),
        )
    neg = nat.template_strings(["", ""], [(1, 5)], cols, 4).to_pylist()
    assert neg[1] == "-0042" == format(-42, "05d")
    for m in (nat, ref):
        with pytest.raises(ValueError):
            m.template_strings(["a"], [(0, 0)], cols, 4)  # literal count
        with pytest.raises(ValueError):
            m.template_strings(["", ""], [(9, 0)], cols, 4)  # missing column
        with pytest.raises(ValueError):
            m.template_strings(["", ""], [(0, 0)], cols, 5)  # wrong n_rows
        with pytest.raises(ValueError):
            m.template_strings(["", ""], [(0, 0)], [pa.array([1.5])], 1)  # float column


def test_template_strings_integer_extremes_and_padding(nat):
    ints = pa.array(
        [0, 1, -1, 9, 10, -10, 99, 100, 12345, -12345, 2**63 - 1, -(2**63), -(2**63) + 1],
        type=pa.int64(),
    )
    n = len(ints)
    for width in (0, 1, 2, 5, 19, 20, 21, 25, 47, 48, 49, 60):
        got = nat.template_strings(["<", ">"], [(0, width)], [ints], n)
        _same(got, ref.template_strings(["<", ">"], [(0, width)], [ints], n))
        want = [f"<{v:0{width}d}>" for v in ints.to_pylist()]
        assert got.to_pylist() == want


@pytest.mark.parametrize("n", [0, 1, 1000, 32_767, 32_768, 40_000, 70_000])
@pytest.mark.parametrize("null_rows", ["none", "first", "last", "spread", "one_task", "all"])
def test_template_strings_nulls_across_the_serial_and_parallel_paths(nat, n, null_rows):
    """Rows of a million-row column are built in tasks; a null in any one of them, or none, must
    land on the right row (the validity buffer is only built when there is a null)."""
    rng = np.random.default_rng(n + 17)
    values = rng.integers(-1000, 100_000, n).astype(object)
    idx = np.arange(n)
    mask = {
        "none": np.zeros(n, bool),
        "first": idx == 0,
        "last": idx == n - 1,
        "spread": idx % 7 == 3,
        "one_task": (idx >= 20_000) & (idx < 20_010),
        "all": np.ones(n, bool),
    }[null_rows]
    values[mask] = None
    col = pa.array(values.tolist(), type=pa.int64())
    texts = pa.array([f"s{i}" for i in range(n)], type=pa.string())
    cols, lits, slots = [col, texts], ["k", "-", "!"], [(0, 4), (1, 0)]
    got = nat.template_strings(lits, slots, cols, n)
    _same(got, ref.template_strings(lits, slots, cols, n))
    checked = pa.array(got)
    assert checked.null_count == int(mask.sum())
    assert checked.is_null().to_pylist() == mask.tolist()


def test_join_strings(nat):
    a = pa.array(["a", None, "c", None])
    b = pa.array(["x", "y", None, None], type=pa.large_string())
    c = pa.array([1, 2, 3, None], type=pa.int64())
    for skip in (False, True):
        got = nat.join_strings([a, b, c], " ", skip)
        _same(got, ref.join_strings([a, b, c], " ", skip))
    assert nat.join_strings([a, b, c], "|", False).to_pylist() == ["a|x|1", None, None, None]
    assert nat.join_strings([a, b, c], "|", True).to_pylist() == ["a|x|1", "y|2", "c|3", ""]
    for m in (nat, ref):
        with pytest.raises(ValueError):
            m.join_strings([a, pa.array(["x"])], ",")


def test_string_case(nat):
    values = [*POOL, "hello WORLD 3rd-place", "McDONALD's", "  lead trail  ", "ǆ ǈ"]
    arr = pa.array(values, type=pa.string())
    for mode in ("upper", "lower", "title"):
        _same(nat.string_case(arr, mode), ref.string_case(arr, mode))
        _same(
            nat.string_case(pa.array(values, pa.large_string()), mode), ref.string_case(arr, mode)
        )
    assert nat.string_case(arr, "title").to_pylist()[9:11] == [
        "Hello World 3rd-Place",
        "Mcdonald'S",
    ]
    for m in (nat, ref):
        with pytest.raises(ValueError):
            m.string_case(arr, "snake")


def test_uuid_and_random_strings(nat):
    k0, k1 = KEYS[3]
    u = nat.uuid4_strings(k0, k1, 5, 2000)
    _same(u, ref.uuid4_strings(k0, k1, 5, 2000))
    import uuid

    parsed = [uuid.UUID(s) for s in u.to_pylist()[:200]]
    assert all(p.version == 4 and p.variant == uuid.RFC_4122 for p in parsed)
    assert len(set(u.to_pylist())) == 2000
    big = nat.uuid4_strings(k0, k1, 0, 100_000).to_pylist()
    assert big[1000:1010] == nat.uuid4_strings(k0, k1, 1000, 10).to_pylist()
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    for length in (0, 1, 4, 9):
        got = nat.random_strings(k0, k1, 3, 1500, length, alphabet)
        _same(got, ref.random_strings(k0, k1, 3, 1500, length, alphabet))
        assert all(len(s) == length and set(s) <= set(alphabet) for s in got.to_pylist())
    _same(
        nat.random_strings(k0, k1, 0, 100, 3, "éß日"), ref.random_strings(k0, k1, 0, 100, 3, "éß日")
    )
    for m in (nat, ref):
        with pytest.raises(ValueError):
            m.random_strings(k0, k1, 0, 3, 2, "")


# ------------------------------------------------------------------ temporal


def _day(d: dt.date) -> int:
    return (d - dt.date(1970, 1, 1)).days


def test_hour_weights_peaks(nat):
    for peaks, std in [([12.0, 18.0], 2.0), ([23.5], 3.0), ([0.0, 6.0, 12.0], 9.0), ([8.0], 0.4)]:
        a = _np(nat.hour_weights_peaks(peaks, std))
        b = _np(ref.hour_weights_peaks(peaks, std))
        np.testing.assert_allclose(a, b, rtol=1e-12, atol=1e-15)
        assert abs(a.sum() - len(peaks)) < 1e-9
    w = _np(nat.hour_weights_peaks([12.0, 18.0], 2.0))
    assert w.argmax() in (11, 12, 17, 18) and w[3] < 1e-3
    for m in (nat, ref):
        with pytest.raises(ValueError):
            m.hour_weights_peaks([], 2.0)
        with pytest.raises(ValueError):
            m.hour_weights_peaks([1.0], 0.0)


def test_day_weights(nat):
    for start, n in [(_day(dt.date(2022, 1, 1)), 1461), (_day(dt.date(1969, 11, 20)), 200), (0, 1)]:
        mw = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
        dw = [1, 1, 1, 1, 2, 3, 3]
        for bucket in (True, False):
            _same(
                nat.day_weights(start, n, mw, dw, bucket), ref.day_weights(start, n, mw, dw, bucket)
            )
    # each (month, weekday) pair carries month_w * dow_w in total
    start, n = _day(dt.date(2022, 1, 1)), 1461
    w = _np(nat.day_weights(start, n, [1.0] * 12, [1.0] * 7, True))
    days = [dt.date(1970, 1, 1) + dt.timedelta(days=start + i) for i in range(n)]
    totals: dict[tuple[int, int], float] = {}
    for d, x in zip(days, w, strict=True):
        totals[(d.month, d.weekday())] = totals.get((d.month, d.weekday()), 0.0) + x
    assert all(abs(v - 1.0) < 1e-9 for v in totals.values())
    for m in (nat, ref):
        with pytest.raises(ValueError):
            m.day_weights(0, 5, [1.0] * 11, [1.0] * 7)


def test_temporal_sample(nat):
    start = _day(dt.date(2023, 1, 1))
    n_days = 365
    month_w = [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 3, 4]
    dow_w = [1, 1, 1, 1, 2, 4, 3]
    dw = nat.day_weights(start, n_days, month_w, dow_w, True)
    hw = nat.hour_weights_peaks([12.0, 18.0], 2.0)
    k0, k1 = KEYS[1]
    for whole in (False, True):
        got = nat.temporal_sample(dw, hw, start, k0, k1, 21, 4000, whole)
        _same(got, ref.temporal_sample(dw, hw, start, k0, k1, 21, 4000, whole))
    got = nat.temporal_sample(dw, hw, start, k0, k1, 0, 300_000)
    assert got.type == pa.timestamp("us")
    ts = _np(got).astype("datetime64[us]")
    assert ts.min() >= np.datetime64("2023-01-01") and ts.max() < np.datetime64("2024-01-01")
    months = ts.astype("datetime64[M]").astype(int) % 12
    freq = np.bincount(months, minlength=12) / len(ts)
    want = np.array(month_w, float) * 1.0
    want = want / want.sum()
    # months differ in day count only through the (month, weekday) bucket rule: each month's mass
    # is its weight
    assert 0.5 * np.abs(freq - want).sum() < 0.01
    hours = (ts - ts.astype("datetime64[D]")).astype("timedelta64[h]").astype(int)
    hf = np.bincount(hours, minlength=24) / len(ts)
    hexp = _np(hw) / _np(hw).sum()
    assert 0.5 * np.abs(hf - hexp).sum() < 0.01
    whole_ts = _np(nat.temporal_sample(dw, hw, start, k0, k1, 0, 1000, True)).astype(
        "datetime64[us]"
    )
    assert (whole_ts.astype("datetime64[s]").astype("datetime64[us]") == whole_ts).all()
    for m in (nat, ref):
        with pytest.raises(ValueError):
            m.temporal_sample(dw, pa.array([1.0] * 5), start, k0, k1, 0, 3)


def test_temporal_rows_do_not_depend_on_the_call_split(nat):
    start = 19_000
    dw = nat.day_weights(start, 100, [1.0] * 12, [1.0] * 7)
    hw = pa.array([1.0] * 24)
    k0, k1 = KEYS[3]
    whole = _np(nat.temporal_sample(dw, hw, start, k0, k1, 0, 100_000)).astype("int64")
    pieces = [
        _np(nat.temporal_sample(dw, hw, start, k0, k1, a, 20_000)).astype("int64")
        for a in range(0, 100_000, 20_000)
    ]
    assert (np.concatenate(pieces) == whole).all()
