"""Where a key lives: the first row of a key column that holds each probe value.

The post-passes (compute phase, business rules) and ``lookup`` all ask this for the primary key of
a parent table. Such a key is almost always a sequence (``start``, ``start + 1``, ...), whose row
is the value minus ``start``: no hash table, no search. Any other integer column is found by
binary search over its sorted values, and any other type through Arrow's ``index_in``. All three
give the same answer: the row of the *first* equal key, or ``-1`` for a null probe or a value no
key equals.

Stable interface: ``dense_start``, ``dense_positions``, ``dense_row_array``, ``first_positions`` and
``first_rows``.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.generation.kernel_relational import dense_rows

Int64s = npt.NDArray[np.int64]


def _flat(values: pa.Array | pa.ChunkedArray) -> pa.Array:
    return values.combine_chunks() if isinstance(values, pa.ChunkedArray) else values


def _integers(values: pa.Array) -> tuple[Int64s, npt.NDArray[np.bool_] | None]:
    """An integer Arrow array as int64 (nulls as 0) and its null mask (``None`` without nulls)."""
    if values.null_count == 0:
        return np.asarray(values.to_numpy(zero_copy_only=False), dtype=np.int64), None
    mask = np.asarray(values.is_null().to_numpy(zero_copy_only=False), dtype=bool)
    filled = pc.fill_null(values, 0)
    return np.asarray(filled.to_numpy(zero_copy_only=False), dtype=np.int64), mask


def dense_start(keys: pa.Array | pa.ChunkedArray) -> int | None:
    """``s`` when ``keys`` is the integers ``s, s + 1, ...`` in order with no null, else ``None``.
    Such keys are unique, and the row of key ``k`` is ``k - s``."""
    if not pa.types.is_signed_integer(keys.type) or len(keys) == 0 or keys.null_count:
        return None
    values, _ = _integers(_flat(keys))
    start = int(values[0])
    if values[-1] - start != len(values) - 1:
        return None
    return start if bool(np.array_equal(values, np.arange(start, start + len(values)))) else None


def dense_positions(probe: pa.Array | pa.ChunkedArray, start: int, size: int) -> Int64s:
    """Rows of a ``size``-row sequence key that begins at ``start`` (see :func:`dense_start`),
    for a signed integer ``probe``: ``-1`` for a null or a value outside the sequence."""
    values, null = _integers(_flat(probe))
    row = values - start
    hit = (row >= 0) & (row < size)
    if null is not None:
        hit &= ~null
    return np.where(hit, row, -1)


def dense_row_array(probe: pa.Array | pa.ChunkedArray, start: int, size: int) -> pa.Array:
    """:func:`dense_positions` as an Arrow array that is null instead of ``-1``: the indices for
    ``take``. One native pass over the probe values."""
    values = _flat(probe)
    if values.type != pa.int64():
        values = values.cast(pa.int64())
    return dense_rows(values, start, size)


def first_rows(probe: pa.Array | pa.ChunkedArray, keys: pa.Array | pa.ChunkedArray) -> pa.Array:
    """:func:`first_positions` as an Arrow array that is null instead of ``-1``."""
    start = dense_start(keys)
    if start is not None and pa.types.is_signed_integer(probe.type):
        return dense_row_array(probe, start, len(keys))
    pos = first_positions(probe, keys)
    miss = pos < 0
    return pa.array(np.where(miss, 0, pos), mask=miss, type=pa.int64())


def first_positions(probe: pa.Array | pa.ChunkedArray, keys: pa.Array | pa.ChunkedArray) -> Int64s:
    """For each probe value, the row of the first key equal to it, or ``-1``."""
    probe, keys = _flat(probe), _flat(keys)
    if len(probe) == 0:
        return np.empty(0, dtype=np.int64)
    if (
        pa.types.is_signed_integer(probe.type)
        and pa.types.is_signed_integer(keys.type)
        and len(keys)
    ):
        start = dense_start(keys)
        if start is not None:
            return dense_positions(probe, start, len(keys))
        values, null = _integers(probe)
        sorted_keys, order = _sorted(keys)
        where = np.searchsorted(sorted_keys, values, side="left")
        clipped = np.minimum(where, len(sorted_keys) - 1)
        hit = (where < len(sorted_keys)) & (sorted_keys[clipped] == values)
        if null is not None:
            hit &= ~null
        return np.where(hit, order[clipped], -1).astype(np.int64)
    found = pc.index_in(probe, value_set=keys)
    return np.asarray(pc.fill_null(found, -1).to_numpy(zero_copy_only=False), dtype=np.int64)


def _sorted(keys: pa.Array) -> tuple[Int64s, Int64s]:
    values, null = _integers(keys)
    if null is not None:  # a null key matches nothing: park it where no probe can reach
        values = np.where(null, np.iinfo(np.int64).min, values)
    order = np.argsort(values, kind="stable").astype(np.int64)
    return values[order], order
