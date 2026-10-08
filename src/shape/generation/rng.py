"""Row-addressed random streams for generation (T-16).

A :class:`RowStream` is one Philox4x64-10 stream keyed by ``(seed, table, column, label)``. Its
outputs are numbered: the stream's ``j``-th 64-bit word is word ``j % 4`` of
``numpy.random.Philox(key=key, counter=j // 4).random_raw(4)``. A row owns ``per_row``
consecutive words, so row ``r`` reads words ``r * per_row .. r * per_row + per_row - 1``. Every
result is therefore a function of ``(key, row)`` alone: the same however the rows are chunked,
read in any order, or split across threads. ``label`` separates the streams one column needs (the
engine's null mask, a strategy's value draw, a rule fix), so no two uses share numbers.

The key is the first 16 bytes of ``blake2b(seed, table, column, label)`` read as two
little-endian 64-bit words ``(k0, k1)``; ``numpy.random.Philox(key=k0 | k1 << 64)``. The native
kernel (``gen/rng.rs``, P4-03) receives ``(k0, k1)`` and reproduces :meth:`RowStream.raw` word for
word; the known-answer oracle is numpy itself.

Stable interface: ``RowStream``, ``stream_key``,
``uniform_from_raw`` and ``normal_from_raw``.

``RowStream`` reads its words and derived values through the selected kernel
(:mod:`shape.kernel.dispatch`): the native one when it is built, else the Python reference. The
two give the same words, uniforms, normals and integer results exactly: ``normal`` is Box-Muller
with the portable ``log`` and ``cos`` of :mod:`shape.kernel.pmath`, so it is the same on every
machine too (#768). ``docs/GENERATION_KERNEL.md`` documents the kernel functions.
"""

from __future__ import annotations

import hashlib

import numpy as np
import numpy.typing as npt

from shape.generation.arrowkit import array as arrow_array
from shape.generation.arrowkit import to_numpy as arrow_numpy
from shape.kernel import pmath
from shape.kernel.dispatch import get_kernel

_TWO_NEG_53 = 2.0**-53


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
    return np.sqrt(-2.0 * pmath.log(u1)) * pmath.cos_turns(u2)


_MASK64 = (1 << 64) - 1


class RowStream:
    """One keyed Philox stream, read by row."""

    __slots__ = ("column", "k0", "k1", "key", "label", "seed", "table")

    def __init__(self, seed: int, table: str, column: str, label: str = "") -> None:
        self.seed, self.table, self.column, self.label = seed, table, column, label
        self.key = stream_key(seed, table, column, label)
        self.k0 = self.key & _MASK64
        self.k1 = self.key >> 64

    def derive(self, suffix: str) -> RowStream:
        """A separate stream for a sub-draw: same seed, table and column, label
        ``"<label>/<suffix>"`` (a family that needs several independent draws per row, such as a
        rejection attempt or a mixture component, takes one derived stream for each)."""
        return RowStream(self.seed, self.table, self.column, f"{self.label}/{suffix}")

    @staticmethod
    def _check(row_start: int, n_rows: int, per_row: int = 1) -> None:
        if row_start < 0 or n_rows < 0 or per_row < 1:
            raise ValueError("row_start and n_rows must be non-negative and per_row positive")

    def raw(self, row_start: int, n_rows: int, per_row: int = 1) -> npt.NDArray[np.uint64]:
        """Words for rows ``row_start .. row_start + n_rows - 1``, shape ``(n_rows, per_row)``."""
        self._check(row_start, n_rows, per_row)
        if n_rows == 0:
            return np.empty((0, per_row), dtype=np.uint64)
        words = get_kernel().philox_words(self.k0, self.k1, row_start, n_rows, per_row)
        flat = np.asarray(arrow_numpy(arrow_array(words)), dtype=np.uint64)
        return flat.reshape(n_rows, per_row)

    def uniform(
        self, row_start: int, n_rows: int, per_row: int = 1, slot: int = 0
    ) -> npt.NDArray[np.float64]:
        """One uniform in ``[0, 1)`` per row (word ``slot`` of the row's ``per_row`` words)."""
        self._check(row_start, n_rows, per_row)
        out = get_kernel().philox_uniform(self.k0, self.k1, row_start, n_rows, per_row, slot)
        return np.asarray(arrow_numpy(arrow_array(out)), dtype=np.float64)

    def normal(
        self, row_start: int, n_rows: int, per_row: int = 2, slot: int = 0
    ) -> npt.NDArray[np.float64]:
        """One standard normal per row (words ``slot`` and ``slot + 1`` of the row)."""
        self._check(row_start, n_rows, per_row)
        out = get_kernel().philox_normal(self.k0, self.k1, row_start, n_rows, per_row, slot)
        return np.asarray(arrow_numpy(arrow_array(out)), dtype=np.float64)
