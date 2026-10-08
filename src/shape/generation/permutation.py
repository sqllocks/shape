"""A keyed pseudo-random permutation of ``0 .. n - 1``, evaluated for any row on its own.

``record_sample`` with ``unique`` must give every row a different record without remembering
what other rows took, so that the record of row ``r`` does not depend on how the table is chunked.
A Feistel network over the next even power of two, with cycle walking back into ``0 .. n - 1``,
is a bijection, so rows ``0 .. m - 1`` (``m <= n``) always get ``m`` distinct positions.

Stable interface: ``permute``.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

_MASK64 = (1 << 64) - 1
_ROUNDS = 6
_C1 = np.uint64(0xBF58476D1CE4E5B9)
_C2 = np.uint64(0x94D049BB133111EB)
_S30, _S27, _S31 = np.uint64(30), np.uint64(27), np.uint64(31)


def _splitmix(x: int) -> int:
    z = (x + 0x9E3779B97F4A7C15) & _MASK64
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK64
    return z ^ (z >> 31)


def _mix(x: npt.NDArray[np.uint64], key: np.uint64) -> npt.NDArray[np.uint64]:
    z = x + key
    z = (z ^ (z >> _S30)) * _C1
    z = (z ^ (z >> _S27)) * _C2
    return z ^ (z >> _S31)


def permute(index: npt.NDArray[np.integer], n: int, key: int) -> npt.NDArray[np.int64]:
    """``P(index)`` for the permutation ``P`` of ``0 .. n - 1`` that ``key`` (a 128-bit integer)
    selects. Every ``index`` must be in ``0 .. n - 1``."""
    if n < 1:
        raise ValueError("n must be at least 1")
    bits = max(2, (n - 1).bit_length())
    bits += bits % 2
    half = bits // 2
    mask = np.uint64((1 << half) - 1)
    shift = np.uint64(half)
    seed = (key ^ (key >> 64)) & _MASK64
    round_keys = [np.uint64(_splitmix(seed + i)) for i in range(_ROUNDS)]

    def encrypt(x: npt.NDArray[np.uint64]) -> npt.NDArray[np.uint64]:
        left, right = x >> shift, x & mask
        for rk in round_keys:
            left, right = right, left ^ (_mix(right, rk) & mask)
        return (left << shift) | right

    walked = encrypt(np.asarray(index, dtype=np.uint64))
    limit = np.uint64(n)
    outside = walked >= limit
    while outside.any():
        walked[outside] = encrypt(walked[outside])
        outside = walked >= limit
    return walked.astype(np.int64)
