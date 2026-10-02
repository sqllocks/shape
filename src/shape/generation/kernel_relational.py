"""Typed wrappers over the row-sequential relational kernels (P4-04d).

These are the passes that need a whole column of a table, because a row's value depends on the
rows before it: the first row of each parent, the version order inside a business key, the
effective dates of a slowly changing dimension, a parent that is full. A strategy computes them
once per table and keeps the result (``Engine.cached``); every chunk then reads its slice. The
kernels are functions of their inputs and the stream key alone, so the result never depends on
threads, chunking or call order. Native and twin results are equal (``docs/GENERATION_KERNEL.md``).

Stable interface: ``first_flags``, ``group_order``, ``scd2_offsets``, ``cap_per_parent``,
``dense_rows`` and ``group_sums``.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]

from shape.generation.arrowkit import array as arrow_array
from shape.generation.arrowkit import to_numpy as arrow_numpy
from shape.kernel.dispatch import get_kernel

from .rng import RowStream

Ints = npt.NDArray[np.int64]


def _ints(values: Any) -> pa.Array:
    return arrow_array(np.ascontiguousarray(values, dtype=np.int64), type=pa.int64())


def _numpy(array: Any) -> Ints:
    out: Ints = np.asarray(arrow_numpy(arrow_array(array)), dtype=np.int64)
    return out


def first_flags(codes: Ints) -> npt.NDArray[np.bool_]:
    """``True`` for the first row of each group. ``codes`` are dense group ids (``0 <= code <
    len(codes)``, as ``pyarrow`` dictionary codes are); a negative code is no group and is never
    first."""
    flags = arrow_array(get_kernel().first_flags(_ints(codes)))
    return np.asarray(arrow_numpy(flags), dtype=np.bool_)


def group_order(codes: Ints, keys: Ints) -> tuple[Ints, Ints, Ints]:
    """``(rank, size, next)`` of every row inside its group sorted by ``keys`` (ties keep row
    order): its 0-based place, the size of the group and the row that follows it (-1 for the last).
    A negative code gives ``(-1, 0, -1)``."""
    rank, size, nxt = get_kernel().group_order(_ints(codes), _ints(keys))
    return _numpy(rank), _numpy(size), _numpy(nxt)


def scd2_offsets(codes: Ints, total_days: int, min_gap: int, stream: RowStream) -> Ints:
    """Day offsets (0 .. ``total_days``) of the effective dates of an SCD type 2 table. A group
    of ``m`` rows (equal codes) gets ``m`` increasing offsets at least ``min_gap`` days apart, in
    row order, drawn from the group's own stream; a negative code gives -1."""
    out = get_kernel().scd2_offsets(_ints(codes), total_days, min_gap, stream.k0, stream.k1)
    return _numpy(out)


def cap_per_parent(indices: Ints, pool: int, max_per_parent: int, stream: RowStream) -> Ints:
    """Parent indices (``0 .. pool - 1``) with at most ``max_per_parent`` rows per parent: a row
    whose parent is full moves to another parent with room, drawn from the stream."""
    out = get_kernel().cap_per_parent(_ints(indices), pool, max_per_parent, stream.k0, stream.k1)
    return _numpy(out)


def dense_rows(keys: pa.Array, start: int, size: int) -> pa.Array:
    """The row of the sequence key ``start, start + 1, ...`` (``size`` rows) that holds each key of
    ``keys`` (int64, nulls allowed): an int64 array that is null for a null key or one outside the
    sequence. Pass it to ``take`` to read the parent's column."""
    out: pa.Array = arrow_array(get_kernel().dense_rows(keys, start, size))
    return out


def group_sums(
    keys: pa.Array, values: pa.Array, start: int, size: int
) -> tuple[pa.Array, pa.Array]:
    """``(sums, counts)`` per row of the sequence key ``start, start + 1, ...`` (``size`` rows):
    the sum (the type of ``values``, int64 or float64) and the number of the non-null ``values`` of
    the child rows whose key in ``keys`` is that row's, added in row order."""
    sums, counts = get_kernel().group_sums(keys, values, start, size)
    return arrow_array(sums), arrow_array(counts)
