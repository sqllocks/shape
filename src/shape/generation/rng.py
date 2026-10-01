"""Row-addressed random streams for generation (T-16).

A :class:`RowStream` is one Philox4x64-10 stream keyed by ``(seed, table, column, label)``. Its
outputs are numbered: the stream's ``j``-th 64-bit word is word ``j % 4`` of
``numpy.random.Philox(key=key, counter=j // 4).random_raw(4)``. A row owns ``per_row``
consecutive words, so row ``r`` reads words ``r * per_row .. r * per_row + per_row - 1``. Every
result is therefore a function of ``(key, row)`` alone: the same however the rows are chunked,
read in any order, or split across threads. ``label`` separates the streams one column needs (the
engine's null mask, a strategy's value draw, a rule fix), so no two uses share numbers.

The key is the first 16 bytes of ``blake2b(seed, table, column, label)`` read as two
little-endian 64-bit words ``(k0, k1)``; ``numpy.random.Philox(key=k0 | k1 << 64)``. This module
is the Python reference. The Rust twin (P4-03, ``gen/rng.rs``) receives ``(k0, k1)`` and must
reproduce :meth:`RowStream.raw` word for word; the known-answer oracle is numpy itself.

Stable interface: ``RowStream``, ``stream_key``, ``uniform_from_raw`` and ``normal_from_raw``.
"""

from __future__ import annotations

import hashlib

import numpy as np
import numpy.typing as npt

_TWO_NEG_53 = 2.0**-53
_TWO_PI = 2.0 * np.pi


def stream_key(seed: int, table: str, column: str, label: str = "") -> int:
    """The 128-bit Philox key of one stream (see the module docstring)."""
    material = f"{int(seed)}\x00{table}\x00{column}\x00{label}".encode()
    return int.from_bytes(hashlib.blake2b(material, digest_size=16).digest(), "little")


def uniform_from_raw(raw: npt.NDArray[np.uint64]) -> npt.NDArray[np.float64]:
    """Doubles in ``[0, 1)`` from 64-bit words: the top 53 bits, scaled."""
    return (raw >> np.uint64(11)).astype(np.float64) * _TWO_NEG_53


def normal_from_raw(raw: npt.NDArray[np.uint64]) -> npt.NDArray[np.float64]:
    """Standard normals by Box-Muller from word pairs: ``raw`` has two words per row (last
    axis); the result has one value per row."""
    u1 = 1.0 - uniform_from_raw(raw[..., 0])  # in (0, 1]: log is finite
    u2 = uniform_from_raw(raw[..., 1])
    return np.sqrt(-2.0 * np.log(u1)) * np.cos(_TWO_PI * u2)


class RowStream:
    """One keyed Philox stream, read by row."""

    __slots__ = ("key",)

    def __init__(self, seed: int, table: str, column: str, label: str = "") -> None:
        self.key = stream_key(seed, table, column, label)

    def raw(self, row_start: int, n_rows: int, per_row: int = 1) -> npt.NDArray[np.uint64]:
        """Words for rows ``row_start .. row_start + n_rows - 1``, shape ``(n_rows, per_row)``."""
        if row_start < 0 or n_rows < 0 or per_row < 1:
            raise ValueError("row_start and n_rows must be non-negative and per_row positive")
        if n_rows == 0:
            return np.empty((0, per_row), dtype=np.uint64)
        first = row_start * per_row
        last = first + n_rows * per_row
        block = first // 4
        words = np.random.Philox(key=self.key, counter=block).random_raw(
            (last + 3) // 4 * 4 - block * 4
        )
        skip = first - block * 4
        return words[skip : skip + n_rows * per_row].reshape(n_rows, per_row)

    def uniform(self, row_start: int, n_rows: int) -> npt.NDArray[np.float64]:
        """One uniform in ``[0, 1)`` per row."""
        return uniform_from_raw(self.raw(row_start, n_rows)[:, 0])

    def normal(self, row_start: int, n_rows: int) -> npt.NDArray[np.float64]:
        """One standard normal per row (two words each)."""
        return normal_from_raw(self.raw(row_start, n_rows, 2))
