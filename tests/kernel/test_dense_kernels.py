"""P4-07: ``dense_rows`` and ``group_sums``, the single-pass key lookup and grouped sum of the
post-passes, against their pure-Python twins and against what they promise. Float sums are added
in row order on both sides, so native and twin must be equal bit for bit."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from shape.generation import kernel_relational
from shape.kernel import dispatch
from shape.kernel.reference import relational as ref


@pytest.fixture(scope="module")
def nat():
    return dispatch._import_native()


def _same(a, b):
    a, b = pa.array(a), pa.array(b)
    assert a.type == b.type
    assert a.equals(b)


keys_lists = st.lists(st.one_of(st.none(), st.integers(min_value=-3, max_value=25)), max_size=80)


def test_dense_rows_known_answer(nat):
    keys = pa.array([5, 7, None, 4, 9, 8], type=pa.int64())
    want = [0, 2, None, None, None, 3]
    assert nat.dense_rows(keys, 5, 4).to_pylist() == want
    assert ref.dense_rows(keys, 5, 4).to_pylist() == want
    assert pa.array(nat.dense_rows(keys, 5, 4)).type == pa.int64()


@settings(max_examples=80, deadline=None)
@given(keys_lists, st.integers(min_value=-2, max_value=10), st.integers(min_value=0, max_value=20))
def test_dense_rows_native_equals_twin(keys, start, size):
    nat = dispatch._import_native()
    column = pa.array(keys, type=pa.int64())
    _same(nat.dense_rows(column, start, size), ref.dense_rows(column, start, size))


def test_dense_rows_takes_the_parents_column(nat):
    parent = pa.array(["a", "b", "c"])
    rows = kernel_relational.dense_rows(pa.array([3, None, 1, 9, 2], type=pa.int64()), 1, 3)
    assert parent.take(rows).to_pylist() == ["c", None, "a", None, "b"]


def test_group_sums_known_answer(nat):
    keys = pa.array([1, 2, 1, None, 9, 2], type=pa.int64())
    values = pa.array([0.5, 1.0, 0.25, 7.0, 3.0, 2.0])
    for impl in (nat, ref):
        sums, counts = impl.group_sums(keys, values, 1, 3)
        assert pa.array(sums).to_pylist() == [0.75, 3.0, 0.0]
        assert pa.array(counts).to_pylist() == [2, 2, 0]
    masked = pa.array([0.5, 1.0, None, 7.0, 3.0, 2.0])
    for impl in (nat, ref):
        sums, counts = impl.group_sums(keys, masked, 1, 3)
        assert pa.array(sums).to_pylist() == [0.5, 3.0, 0.0]
        assert pa.array(counts).to_pylist() == [1, 2, 0]


def test_group_sums_of_integers_stays_integer_and_wraps(nat):
    keys = pa.array([1, 1, 2], type=pa.int64())
    values = pa.array([2**63 - 1, 1, 5], type=pa.int64())
    for impl in (nat, ref):
        sums, _ = impl.group_sums(keys, values, 1, 2)
        assert pa.array(sums).type == pa.int64()
        assert pa.array(sums).to_pylist() == [-(2**63), 5]


@settings(max_examples=80, deadline=None)
@given(
    st.lists(
        st.tuples(
            st.one_of(st.none(), st.integers(min_value=0, max_value=12)),
            st.one_of(st.none(), st.floats(min_value=-1e6, max_value=1e6, allow_nan=False)),
        ),
        max_size=100,
    ),
    st.integers(min_value=0, max_value=3),
    st.integers(min_value=0, max_value=10),
)
def test_group_sums_native_equals_twin_bit_for_bit(rows, start, size):
    nat = dispatch._import_native()
    keys = pa.array([k for k, _ in rows], type=pa.int64())
    values = pa.array([v for _, v in rows], type=pa.float64())
    for got, want in zip(
        nat.group_sums(keys, values, start, size),
        ref.group_sums(keys, values, start, size),
        strict=True,
    ):
        _same(got, want)
    ints = pa.array([None if v is None else int(v) for _, v in rows], type=pa.int64())
    for got, want in zip(
        nat.group_sums(keys, ints, start, size),
        ref.group_sums(keys, ints, start, size),
        strict=True,
    ):
        _same(got, want)


def test_group_sums_agrees_with_arrows_grouped_sum():
    rng = np.random.default_rng(3)
    keys = pa.array(rng.integers(1, 501, 40_000), type=pa.int64())
    values = pa.array(np.round(rng.random(40_000) * 100, 2))
    sums, counts = kernel_relational.group_sums(keys, values, 1, 500)
    grouped = (
        pa.table({"k": keys, "v": values}).group_by("k").aggregate([("v", "sum"), ("v", "count")])
    )
    by_key = dict(
        zip(
            grouped["k"].to_pylist(),
            zip(grouped["v_sum"].to_pylist(), grouped["v_count"].to_pylist(), strict=True),
            strict=True,
        )
    )
    for row, (total, n) in enumerate(zip(sums.to_pylist(), counts.to_pylist(), strict=True)):
        want_sum, want_n = by_key.get(row + 1, (0.0, 0))
        assert n == want_n
        assert total == pytest.approx(want_sum, rel=0, abs=1e-6)


def test_bad_arguments_are_refused(nat):
    for impl in (nat, ref):
        with pytest.raises(ValueError, match="int64"):
            impl.dense_rows(pa.array([1.0]), 0, 1)
        with pytest.raises(ValueError, match="same length"):
            impl.group_sums(pa.array([1], type=pa.int64()), pa.array([1.0, 2.0]), 1, 1)
        with pytest.raises(ValueError, match="int64 or float64"):
            impl.group_sums(pa.array([1], type=pa.int64()), pa.array(["x"]), 1, 1)
        with pytest.raises(ValueError, match="negative"):
            impl.group_sums(pa.array([1], type=pa.int64()), pa.array([1.0]), 1, -1)
