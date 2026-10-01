"""Pure-Python twin of the row-sequential relational kernels (P4-04d,
``rust/shape-kernel/src/gen/relational.rs``).

Every function has the name, arguments and result of its ``shape._kernel`` counterpart. All
results are integers or flags, so they equal the native kernel's bit for bit. The twin favours
clarity over speed (one Python loop over the groups).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]

from .gen import _below, _words

_MASK = (1 << 64) - 1
_CAP_ATTEMPTS = 64


def _ints(a: Any, what: str) -> npt.NDArray[np.int64]:
    arr = a if isinstance(a, pa.Array) else pa.array(a)
    if not pa.types.is_int64(arr.type):
        raise ValueError(f"{what} must be an int64 array")
    if arr.null_count:
        raise ValueError(f"{what} must not contain nulls")
    return np.asarray(arr.to_numpy(zero_copy_only=False), dtype=np.int64)


def _check_codes(codes: npt.NDArray[np.int64]) -> None:
    if codes.size and int(codes.max()) >= codes.size:
        raise ValueError("group codes must be dense: below the number of rows")


def _groups(codes: npt.NDArray[np.int64]) -> list[npt.NDArray[np.int64]]:
    """The rows of every group (negative codes dropped), in row order, by increasing code."""
    valid = np.flatnonzero(codes >= 0)
    order = valid[np.argsort(codes[valid], kind="stable")]
    cuts = np.flatnonzero(np.diff(codes[order])) + 1
    return [g for g in np.split(order, cuts) if g.size]


def first_flags(codes: Any) -> pa.Array:
    c = _ints(codes, "codes")
    _check_codes(c)
    seen: set[int] = set()
    flags = []
    for v in c.tolist():
        first = v >= 0 and v not in seen
        if v >= 0:
            seen.add(v)
        flags.append(first)
    return pa.array(flags, type=pa.bool_())


def group_order(codes: Any, keys: Any) -> tuple[pa.Array, pa.Array, pa.Array]:
    c, k = _ints(codes, "codes"), _ints(keys, "keys")
    if c.size != k.size:
        raise ValueError("codes and keys must have the same length")
    _check_codes(c)
    rank = np.full(c.size, -1, dtype=np.int64)
    size = np.zeros(c.size, dtype=np.int64)
    nxt = np.full(c.size, -1, dtype=np.int64)
    for rows in _groups(c):
        ordered = rows[np.argsort(k[rows], kind="stable")]
        rank[ordered] = np.arange(ordered.size)
        size[ordered] = ordered.size
        nxt[ordered[:-1]] = ordered[1:]
    return pa.array(rank), pa.array(size), pa.array(nxt)


def _mix64(z: int) -> int:
    z ^= z >> 30
    z = (z * 0xBF58476D1CE4E5B9) & _MASK
    z ^= z >> 27
    z = (z * 0x94D049BB133111EB) & _MASK
    return z ^ (z >> 31)


def _group_key(k0: int, k1: int, anchor: int) -> tuple[int, int]:
    return (
        _mix64(k0 ^ _mix64(anchor)),
        _mix64(k1 ^ ((anchor * 0x9E3779B97F4A7C15) & _MASK) ^ 0xD1B54A32D192ED03),
    )


def scd2_offsets(codes: Any, total_days: int, min_gap: int, k0: int, k1: int) -> pa.Array:
    if total_days < 0 or min_gap < 0:
        raise ValueError("total_days and min_gap must be non-negative")
    c = _ints(codes, "codes")
    _check_codes(c)
    result = np.full(c.size, -1, dtype=np.int64)
    for rows in _groups(c):
        m = int(rows.size)
        g0, g1 = _group_key(int(k0), int(k1), int(rows[0]))
        words = _words(g0, g1, 0, m, 1)
        if m == 1:
            result[rows[0]] = _below(words, max(total_days, 1))[0]
            continue
        usable = max(total_days - min_gap * (m - 1), m)
        offsets = np.sort(_below(words, usable))
        result[rows] = np.minimum(offsets + min_gap * np.arange(m, dtype=np.int64), total_days)
    return pa.array(result)


def cap_per_parent(indices: Any, pool: int, max_per_parent: int, k0: int, k1: int) -> pa.Array:
    if pool < 1 or max_per_parent < 1:
        raise ValueError("pool and max_per_parent must be positive")
    idx = _ints(indices, "indices")
    if idx.size and (int(idx.min()) < 0 or int(idx.max()) >= pool):
        raise ValueError("indices must lie in 0..pool")
    cap = min(int(max_per_parent), 2**32 - 1)
    counts = np.zeros(pool, dtype=np.int64)
    full = 0
    out = np.empty(idx.size, dtype=np.int64)
    for row, index in enumerate(idx.tolist()):
        chosen = index
        if counts[chosen] >= cap:
            first = 0
            found = False
            for attempt in range(_CAP_ATTEMPTS):
                word = _words(int(k0), int(k1), row * _CAP_ATTEMPTS + attempt, 1, 1)
                candidate = int(_below(word, pool)[0])
                if attempt == 0:
                    first = candidate
                    if full == pool:
                        chosen, found = candidate, True
                        break
                if counts[candidate] < cap:
                    chosen, found = candidate, True
                    break
            if not found:
                chosen = first
                for k in range(pool):
                    if counts[(first + k) % pool] < cap:
                        chosen = (first + k) % pool
                        break
        counts[chosen] += 1
        if counts[chosen] == cap:
            full += 1
        out[row] = chosen
    return pa.array(out)


def _nullable_ints(a: Any, what: str) -> pa.Array:
    arr = a if isinstance(a, pa.Array) else pa.array(a)
    if not pa.types.is_int64(arr.type):
        raise ValueError(f"{what} must be an int64 array")
    return arr


def dense_rows(keys: Any, start: int, size: int) -> pa.Array:
    arr = _nullable_ints(keys, "keys")
    values = np.asarray(arr.fill_null(0).to_numpy(zero_copy_only=False), dtype=np.int64)
    valid = (values >= start) & (values - start < size)
    if arr.null_count:
        valid &= np.asarray(arr.is_valid().to_numpy(zero_copy_only=False), dtype=bool)
    rows = np.where(valid, values - start, 0)
    return pa.array(rows, mask=~valid, type=pa.int64())


def group_sums(keys: Any, values: Any, start: int, size: int) -> tuple[pa.Array, pa.Array]:
    if size < 0:
        raise ValueError("size must not be negative")
    k = _nullable_ints(keys, "keys")
    v = values if isinstance(values, pa.Array) else pa.array(values)
    if not (pa.types.is_int64(v.type) or pa.types.is_float64(v.type)):
        raise ValueError("values must be an int64 or float64 array")
    if len(v) != len(k):
        raise ValueError("keys and values must have the same length")
    key_values = np.asarray(k.fill_null(0).to_numpy(zero_copy_only=False), dtype=np.int64)
    keep = (key_values >= start) & (key_values - start < size)
    if k.null_count:
        keep &= np.asarray(k.is_valid().to_numpy(zero_copy_only=False), dtype=bool)
    if v.null_count:
        keep &= np.asarray(v.is_valid().to_numpy(zero_copy_only=False), dtype=bool)
    rows = key_values[keep] - start
    amounts = np.asarray(v.fill_null(0).to_numpy(zero_copy_only=False))[keep]
    sums = np.zeros(size, dtype=amounts.dtype)
    counts = np.zeros(size, dtype=np.int64)
    with np.errstate(over="ignore"):
        np.add.at(sums, rows, amounts)  # unbuffered: adds in row order, like the native loop
    np.add.at(counts, rows, 1)
    return pa.array(sums), pa.array(counts)
