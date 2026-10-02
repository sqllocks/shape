"""P6-01-perf round 4: the fused generation kernels (``uniform_index``, ``pool_pick``,
``alias_pool``, ``alias_values``, ``compose_strings``) against their twins and against the
unfused composition of older kernel calls they replace (same values, bit for bit)."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest

from shape.generation import kernel_ops
from shape.generation.rng import RowStream
from shape.kernel import dispatch
from shape.kernel.reference import gen as ref

M64 = (1 << 64) - 1
KEYS = [(0, 0), (1, 2), (M64, M64), (0x0123456789ABCDEF, 0xFEDCBA9876543210)]


@pytest.fixture(scope="module")
def nat():
    return dispatch._import_native()


def _same(a, b):
    a, b = pa.array(a), pa.array(b)
    assert a.type == b.type
    assert a.equals(b), (a[:5], b[:5])


def _uniform(nat, k0, k1, start, n):
    return np.asarray(pa.array(nat.philox_uniform(k0, k1, start, n)).to_numpy(zero_copy_only=False))


def _formula(u: np.ndarray, size: int) -> np.ndarray:
    """What every caller wrote before the fused call: ``min(int(u * size), size - 1)``."""
    return np.minimum((u * size).astype(np.int64), size - 1)


# ------------------------------------------------------------------ uniform_index


@pytest.mark.parametrize("k0,k1", KEYS)
@pytest.mark.parametrize("size", [1, 2, 3, 7, 49, 1000, 999_983, 2**31 + 11, 2**40 + 5])
def test_uniform_index_is_the_formula_of_the_uniform(nat, k0, k1, size):
    for start, n in [(0, 0), (0, 1), (3, 500), (1_000_003, 777)]:
        got = nat.uniform_index(k0, k1, start, n, size)
        assert pa.array(got).type == pa.int64()
        want = _formula(_uniform(nat, k0, k1, start, n), size)
        assert (np.asarray(pa.array(got).to_numpy()) == want).all()
        _same(got, ref.uniform_index(k0, k1, start, n, size))
        if n:
            assert 0 <= int(want.min()) and int(want.max()) < size


def test_uniform_index_words_per_row_and_slot(nat):
    k0, k1 = KEYS[3]
    for per_row, slot in [(1, 0), (2, 0), (2, 1), (5, 4)]:
        got = nat.uniform_index(k0, k1, 11, 300, 97, per_row, slot)
        words = np.asarray(pa.array(nat.philox_words(k0, k1, 11, 300, per_row)).to_numpy())
        u = (words.reshape(300, per_row)[:, slot] >> np.uint64(11)).astype(np.float64) * 2.0**-53
        assert (np.asarray(pa.array(got).to_numpy()) == _formula(u, 97)).all()
        _same(got, ref.uniform_index(k0, k1, 11, 300, 97, per_row, slot))


def test_uniform_index_does_not_depend_on_chunking_or_threads(nat):
    k0, k1 = KEYS[1]
    n = 200_000
    whole = np.asarray(pa.array(nat.uniform_index(k0, k1, 0, n, 1234)).to_numpy())
    cuts = [0, 1, 4095, 8192, 65_537, 150_000, n]
    parts = [
        np.asarray(pa.array(nat.uniform_index(k0, k1, a, b - a, 1234)).to_numpy())
        for a, b in zip(cuts, cuts[1:], strict=False)
    ]
    assert (np.concatenate(parts) == whole).all()


def test_uniform_index_rejects_bad_arguments(nat):
    for module in (nat, ref):
        with pytest.raises(ValueError):
            module.uniform_index(0, 0, 0, 5, 0)
        with pytest.raises(ValueError):
            module.uniform_index(0, 0, 0, 5, 3, 1, 1)  # slot does not fit


def test_kernel_ops_uniform_index_equals_the_old_numpy_draw():
    stream = RowStream(1042, "t", "c", "fk")
    for size in (1, 5, 30_000):
        got = kernel_ops.uniform_index(stream, 17, 4000, size)
        u = stream.uniform(17, 4000)
        assert (got == np.minimum((u * size).astype(np.int64), size - 1)).all()
        assert got.dtype == np.int64


# ------------------------------------------------------------------ pool_pick


def _pool(n: int) -> pa.Array:
    return pa.array([f"entry {i} é" for i in range(n)], type=pa.string())


@pytest.mark.parametrize("k0,k1", KEYS)
@pytest.mark.parametrize("n_pool", [1, 3, 5000])
def test_pool_pick_is_pool_take_of_the_uniform_index(nat, k0, k1, n_pool):
    pool = _pool(n_pool)
    for start, n in [(0, 0), (0, 1), (5, 900), (70_000, 40_000)]:
        got = nat.pool_pick(pool, k0, k1, start, n)
        index = pa.array(nat.uniform_index(k0, k1, start, n, n_pool))
        _same(got, nat.pool_take(pool, index))
        if n <= 900:
            _same(got, ref.pool_pick(pool, k0, k1, start, n))


def test_pool_pick_errors(nat):
    for module in (nat, ref):
        with pytest.raises(ValueError):
            module.pool_pick(pa.array([], type=pa.string()), 0, 0, 0, 3)
        with pytest.raises(ValueError):
            module.pool_pick(pa.array([1, 2], type=pa.int64()), 0, 0, 0, 3)


# ------------------------------------------------------------------ alias_pool / alias_values


@pytest.mark.parametrize("k0,k1", KEYS[:3])
def test_alias_pick_equals_alias_sample_then_gather(nat, k0, k1):
    weights = pa.array([5.0, 1.0, 0.0, 2.5, 0.5, 9.0], type=pa.float64())
    prob, alias = nat.alias_build(weights)
    pool = _pool(6)
    values = pa.array([0.5, -1.0, float("nan"), 1e300, 0.0, 2.0], type=pa.float64())
    for start, n, per_row, slot in [
        (0, 0, 2, 0),
        (3, 700, 2, 0),
        (0, 500, 4, 2),
        (9, 50_000, 2, 0),
    ]:
        drawn = pa.array(nat.alias_sample(prob, alias, k0, k1, start, n, per_row, slot))
        got_pool = nat.alias_pool(prob, alias, pool, k0, k1, start, n, per_row, slot)
        _same(got_pool, nat.pool_take(pool, drawn))
        got_values = nat.alias_values(prob, alias, values, k0, k1, start, n, per_row, slot)
        idx = np.asarray(drawn.to_numpy())
        want = pa.array(np.asarray(values.to_numpy())[idx], type=pa.float64())
        assert pa.array(got_values).type == pa.float64()
        assert np.array_equal(
            np.asarray(pa.array(got_values).to_numpy()), np.asarray(want.to_numpy()), equal_nan=True
        )
        if n <= 700:
            _same(got_pool, ref.alias_pool(prob, alias, pool, k0, k1, start, n, per_row, slot))
            assert np.array_equal(
                np.asarray(pa.array(got_values).to_numpy()),
                np.asarray(
                    pa.array(
                        ref.alias_values(prob, alias, values, k0, k1, start, n, per_row, slot)
                    ).to_numpy()
                ),
                equal_nan=True,
            )


def test_alias_pick_rejects_a_pool_of_another_size(nat):
    prob, alias = nat.alias_build(pa.array([1.0, 2.0], type=pa.float64()))
    for module in (nat, ref):
        with pytest.raises(ValueError):
            module.alias_pool(prob, alias, _pool(3), 0, 0, 0, 5)
        with pytest.raises(ValueError):
            module.alias_values(prob, alias, pa.array([1.0], type=pa.float64()), 0, 0, 0, 5)


# ------------------------------------------------------------------ compose_strings


def _pieces(nat, n_pool=40):
    names = _pool(n_pool)
    col = pa.array(["Mary Ann", None, "JO", "Ed  Wu"] * 3, type=pa.string())
    ids = pa.array(list(range(-6, 6)), type=pa.int64())
    return names, col, ids


def test_compose_equals_the_unfused_composition(nat):
    """Phone-like, e-mail-like and address-like strings: the old path drew the integers and the
    pool picks with numpy and joined them with ``template_strings``; the fused call is equal."""
    k = {name: (i + 1, 0x9E3779B97F4A7C15 * (i + 2) & M64) for i, name in enumerate("abcde")}
    n, start = 3000, 41
    pool = _pool(37)

    def ints(key, low, high):
        u = _uniform(nat, *key, start, n)
        return low + _formula(u, high - low)

    area, exch, sub = ints(k["a"], 200, 999), ints(k["b"], 200, 999), ints(k["c"], 1000, 9999)
    want = nat.template_strings(
        ["(", ") ", "-", ""],
        [(0, 0), (1, 0), (2, 0)],
        [pa.array(area), pa.array(exch), pa.array(sub)],
        n,
    )
    pieces = [("int", *k["a"], 200, 999, 0, None, None), ("int", *k["b"], 200, 999, 0, None, None)]
    pieces.append(("int", *k["c"], 1000, 9999, 0, None, None))
    got = nat.compose_strings(["(", ") ", "-", ""], pieces, start, n)
    _same(got, want)
    _same(pa.array(got)[:200], ref.compose_strings(["(", ") ", "-", ""], pieces, start, 200))

    # SSN: zero padded, with 666 written as 665
    area = ints(k["a"], 1, 900)
    area = np.where(area == 666, 665, area)
    want = nat.template_strings(
        ["", "-", "-", ""],
        [(0, 3), (1, 2), (2, 4)],
        [pa.array(area), pa.array(ints(k["b"], 1, 100)), pa.array(ints(k["c"], 1, 10_000))],
        n,
    )
    pieces = [
        ("int", *k["a"], 1, 900, 3, 666, 665),
        ("int", *k["b"], 1, 100, 2, None, None),
        ("int", *k["c"], 1, 10_000, 4, None, None),
    ]
    got = nat.compose_strings(["", "-", "-", ""], pieces, start, n)
    _same(got, want)
    assert all(s[:3] != "666" for s in got.to_pylist())

    # pool picks and a pool pick next to a number
    index = np.asarray(pa.array(nat.uniform_index(*k["d"], start, n, 37)).to_numpy())
    want = nat.template_strings(
        ["#", " ", ""],
        [(0, 0), (1, 0)],
        [pa.array(ints(k["a"], 100, 9999)), nat.pool_take(pool, pa.array(index))],
        n,
    )
    pieces = [("int", *k["a"], 100, 9999, 0, None, None), ("pool", pool, *k["d"])]
    _same(nat.compose_strings(["#", " ", ""], pieces, start, n), want)


def test_compose_columns_nulls_and_slugs(nat):
    names, col, ids = _pieces(nat)
    n = 12
    pieces = [("col", col, 0, True), ("col", ids, 4, False), ("pool", names, 3, 4)]
    lits = ["<", "|", "#", ">"]
    got = nat.compose_strings(lits, pieces, 5, n)
    _same(got, ref.compose_strings(lits, pieces, 5, n))
    rows = got.to_pylist()
    assert rows[1] is None and rows[5] is None  # a null column makes the row null
    assert rows[0].startswith("<maryann|-006#entry ")
    assert rows[3].startswith("<edwu|-003#")  # two spaces gone, lower case
    assert rows[2].startswith("<jo|-004#")


def test_compose_large_pieces_take_the_parallel_path(nat):
    """70,000 rows are built in tasks on several threads; the rows equal the same rows built in
    small calls (a row's value depends on its own index only) and a hand-built expectation."""
    n = 70_000
    pool = _pool(11)
    col = pa.array([f"A b{i % 13}" for i in range(n)], type=pa.large_string())

    def pieces(c):
        return [("pool", pool, 7, 8), ("col", c, 0, True), ("int", 1, 2, 0, 100, 2, None, None)]

    lits = ["", ".", "@", ""]
    got = pa.array(nat.compose_strings(lits, pieces(col), 123, n))
    assert got.null_count == 0 and len(got) == n
    cuts = [0, 1, 4095, 16_384, 40_000, n]
    parts = [
        pa.array(nat.compose_strings(lits, pieces(col[a:b]), 123 + a, b - a))
        for a, b in zip(cuts, cuts[1:], strict=False)
    ]
    _same(got, pa.concat_arrays(parts))
    picks = pa.array(nat.pool_pick(pool, 7, 8, 123, n)).to_pylist()
    ints = pa.array(nat.uniform_index(1, 2, 123, n, 100)).to_pylist()
    want = [f"{p}.ab{i % 13}@{v:02d}" for i, (p, v) in enumerate(zip(picks, ints, strict=True))]
    assert got.to_pylist() == want


def test_compose_errors(nat):
    names, col, ids = _pieces(nat)
    accent = pa.array(["é"] * 12, type=pa.string())
    bad = [
        (["a"], [("col", col, 0, False)], 12),  # literal count
        (["", ""], [("col", col, 0, False)], 5),  # wrong n_rows
        (["", ""], [("col", accent, 0, True)], 12),  # a slug column must be ASCII
        (["", ""], [("int", 0, 0, 5, 5, 0, None, None)], 12),  # low >= high
        (["", ""], [("pool", pa.array([], type=pa.string()), 0, 0)], 12),  # empty pool
        (["", ""], [("pool", ids, 0, 0)], 12),  # an integer pool
        (["", ""], [("nope", col)], 12),  # unknown piece
        (["", ""], [("col", pa.array([1.5] * 12), 0, False)], 12),  # float column
    ]
    for module in (nat, ref):
        for lits, pieces, n in bad:
            with pytest.raises((ValueError, TypeError)):
                module.compose_strings(lits, pieces, 0, n)


def test_kernel_ops_compose_matches_the_kernel(nat):
    stream_a = RowStream(7, "t", "c", "area")
    pieces = [
        kernel_ops.IntPiece(stream_a, 1, 900, 3, (666, 665)),
        kernel_ops.PoolPiece(_pool(5), RowStream(7, "t", "c", "p")),
        kernel_ops.ColumnPiece(pa.array(["Ab c"] * 20), 0, True),
    ]
    got = kernel_ops.compose_strings(["", "-", "-", ""], pieces, 9, 20)
    spec = [
        ("int", stream_a.k0, stream_a.k1, 1, 900, 3, 666, 665),
        ("pool", _pool(5), *(RowStream(7, "t", "c", "p").k0, RowStream(7, "t", "c", "p").k1)),
        ("col", pa.array(["Ab c"] * 20), 0, True),
    ]
    _same(got, nat.compose_strings(["", "-", "-", ""], spec, 9, 20))
