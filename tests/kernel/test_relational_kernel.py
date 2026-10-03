"""P4-04d: the row-sequential relational kernels (first row of a parent, version order inside a
business key, SCD2 effective dates, parent caps) against their pure-Python twins, plus what each
one promises. Every result is an integer or a flag, so native and twin must be equal."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from shape.kernel import dispatch
from shape.kernel.reference import relational as ref

KEY = (0x0123456789ABCDEF, 0xFEDCBA9876543210)


@pytest.fixture(scope="module")
def nat():
    return dispatch._import_native()


def _i64(values):
    return pa.array(np.asarray(values, dtype=np.int64))


def _same(a, b):
    assert pa.array(a).type == pa.array(b).type
    assert pa.array(a).equals(pa.array(b))


codes_lists = st.lists(st.integers(min_value=-1, max_value=40), min_size=0, max_size=120).map(
    lambda xs: [x if x < len(xs) else len(xs) - 1 for x in xs]
)


# ------------------------------------------------------------------ first_flags


def test_first_flags_known_answer(nat):
    codes = _i64([0, 1, 0, 2, 1, 0, -1, -1])
    want = [True, True, False, True, False, False, False, False]
    assert nat.first_flags(codes).to_pylist() == want
    assert ref.first_flags(codes).to_pylist() == want


@settings(max_examples=60, deadline=None)
@given(codes_lists)
def test_first_flags_native_equals_twin(codes):
    nat = dispatch._import_native()
    _same(nat.first_flags(_i64(codes)), ref.first_flags(_i64(codes)))


def test_first_flags_rejects_sparse_codes(nat):
    for impl in (nat, ref):
        with pytest.raises(ValueError, match="dense"):
            impl.first_flags(_i64([0, 5]))
        with pytest.raises(ValueError, match="int64"):
            impl.first_flags(pa.array([0.0, 1.0]))


# ------------------------------------------------------------------ group_order


def test_group_order_known_answer(nat):
    codes, keys = _i64([0, 1, 0, 0, 1, -1]), _i64([30, 5, 10, 10, 6, 0])
    for impl in (nat, ref):
        rank, size, nxt = (a.to_pylist() for a in impl.group_order(codes, keys))
        assert rank == [2, 0, 0, 1, 1, -1]
        assert size == [3, 2, 3, 3, 2, 0]
        assert nxt == [-1, 4, 3, 0, -1, -1]


@settings(max_examples=60, deadline=None)
@given(codes_lists, st.data())
def test_group_order_native_equals_twin(codes, data):
    nat = dispatch._import_native()
    keys = data.draw(
        st.lists(st.integers(-5, 5), min_size=len(codes), max_size=len(codes)), label="keys"
    )
    for a, b in zip(
        nat.group_order(_i64(codes), _i64(keys)),
        ref.group_order(_i64(codes), _i64(keys)),
        strict=True,
    ):
        _same(a, b)


def test_group_order_length_mismatch(nat):
    for impl in (nat, ref):
        with pytest.raises(ValueError, match="same length"):
            impl.group_order(_i64([0, 0]), _i64([1]))


# ------------------------------------------------------------------ scd2_offsets


@pytest.mark.parametrize("total_days,min_gap", [(0, 0), (1, 1), (30, 0), (1000, 3), (1095, 1)])
def test_scd2_offsets_native_equals_twin(nat, total_days, min_gap):
    rng = np.random.default_rng(total_days + min_gap)
    # group sizes 1..12 with a few skipped codes and null rows
    codes = rng.integers(-1, 60, size=500)
    codes = np.where(codes >= 500, -1, codes)
    a = nat.scd2_offsets(_i64(codes), total_days, min_gap, *KEY)
    b = ref.scd2_offsets(_i64(codes), total_days, min_gap, *KEY)
    _same(a, b)


def test_scd2_offsets_are_ordered_gapped_and_in_range(nat):
    codes = np.arange(900) % 90  # 90 groups of 10, rows interleaved
    off = np.asarray(nat.scd2_offsets(_i64(codes), 1000, 4, *KEY).to_pylist())
    for g in range(90):
        days = off[g::90]
        assert (np.diff(days) >= 4).all(), days
        assert days.min() >= 0 and days.max() <= 1000


def test_scd2_offsets_single_version_and_null_keys(nat):
    for impl in (nat, ref):
        off = impl.scd2_offsets(_i64([0, 1, -1, 3, 3]), 100, 1, *KEY).to_pylist()
        assert off[2] == -1
        assert 0 <= off[0] < 100 and 0 <= off[1] < 100
        assert off[3] < off[4] or off[4] == 100


def test_scd2_offsets_depend_on_the_key_and_the_first_row_of_the_group(nat):
    codes = _i64([0, 1, 0, 1, 2, 2, 2])
    base = nat.scd2_offsets(codes, 5000, 1, *KEY).to_pylist()
    other = nat.scd2_offsets(codes, 5000, 1, KEY[0], KEY[1] + 1).to_pylist()
    assert base != other
    # a group's dates come from its first row: appending rows of other groups changes nothing
    longer = nat.scd2_offsets(_i64([0, 1, 0, 1, 2, 2, 2, 3, 4, 5]), 5000, 1, *KEY).to_pylist()
    assert longer[:7] == base


def test_scd2_offsets_rejects_negative_arguments(nat):
    for impl in (nat, ref):
        with pytest.raises(ValueError, match="non-negative"):
            impl.scd2_offsets(_i64([0]), -1, 1, *KEY)
        with pytest.raises(ValueError, match="non-negative"):
            impl.scd2_offsets(_i64([0]), 10, -1, *KEY)


# ------------------------------------------------------------------ cap_per_parent


@pytest.mark.parametrize("pool,cap,n", [(10, 40, 300), (50, 3, 120), (7, 1, 7), (4, 2, 30)])
def test_cap_per_parent_native_equals_twin(nat, pool, cap, n):
    rng = np.random.default_rng(pool * 31 + cap)
    idx = np.minimum((rng.pareto(1.2, size=n) * 2).astype(np.int64), pool - 1)
    a = nat.cap_per_parent(_i64(idx), pool, cap, *KEY)
    b = ref.cap_per_parent(_i64(idx), pool, cap, *KEY)
    _same(a, b)


def test_cap_per_parent_enforces_the_cap_and_moves_only_the_surplus(nat):
    rng = np.random.default_rng(3)
    idx = np.minimum((rng.pareto(1.2, size=5000) * 4).astype(np.int64), 99)
    out = np.asarray(nat.cap_per_parent(_i64(idx), 100, 80, *KEY).to_pylist())
    counts = np.bincount(out, minlength=100)
    assert counts.max() <= 80 and counts.sum() == 5000
    assert (out[:30] == idx[:30]).all()  # no parent is full yet: rows keep their parent
    assert (out != idx).sum() >= (np.bincount(idx, minlength=100) - 80).clip(0).sum()


def test_cap_per_parent_spreads_the_surplus_when_every_parent_is_full(nat):
    for impl in (nat, ref):
        out = np.asarray(impl.cap_per_parent(_i64([0] * 60), 10, 5, *KEY).to_pylist())
        assert len(out) == 60 and out.min() >= 0 and out.max() <= 9
        assert np.bincount(out, minlength=10).min() >= 5


def test_cap_per_parent_rejects_bad_arguments(nat):
    for impl in (nat, ref):
        with pytest.raises(ValueError, match="positive"):
            impl.cap_per_parent(_i64([0]), 0, 1, *KEY)
        with pytest.raises(ValueError, match="positive"):
            impl.cap_per_parent(_i64([0]), 1, 0, *KEY)
        with pytest.raises(ValueError, match="0..pool"):
            impl.cap_per_parent(_i64([3]), 3, 1, *KEY)


@pytest.mark.parametrize(
    "call",
    [
        lambda m: m.first_flags(pa.array([0, None, 0])),
        lambda m: m.group_order(pa.array([0, 0, None]), pa.array([1, 2, 3])),
        lambda m: m.group_order(pa.array([0, 0, 0]), pa.array([1, None, 3])),
        lambda m: m.scd2_offsets(pa.array([0, None, 0]), 10, 1, 1, 2),
        lambda m: m.cap_per_parent(pa.array([0, None, 0]), 3, 1, 1, 2),
        lambda m: m.alias_sample(pa.array([1.0, 1.0]), pa.array([0, None]), 1, 2, 0, 3),
        lambda m: m.lognorm_probe(pa.array([1.0, None, 3.0]), 0.0),
    ],
    ids=["first_flags", "group_order_codes", "group_order_keys", "scd2", "cap", "alias", "probe"],
)
def test_nulls_in_dense_inputs_are_value_errors_in_both_kernels(call):
    # Regression #550: the native kernel read the value under a null (usually 0).
    from shape.kernel import dispatch, reference

    for mod in (dispatch._import_native(), reference):
        with pytest.raises(ValueError, match="null"):
            call(mod)
