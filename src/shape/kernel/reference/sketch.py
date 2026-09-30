"""Pure-Python twins of the native sketch classes ``shape._kernel.{Hll, Kll, SpaceSaving}``.

They give the Python sketches of ``pysketch`` (which define the exact
semantics) the same batch-oriented API as the Rust classes: ``update_array`` hashes an Arrow
array with the canonical hash (T-13) and skips nulls and NaN.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]

from .hashing import hash_array


def _sk() -> Any:
    """The Python sketches define the semantics; imported lazily because they import the
    kernel's hashing at module level."""
    from . import pysketch

    return pysketch


def _valid_hashes(array: Any) -> list[int]:
    return [int(h) for h in hash_array(array, 0).to_pylist() if h is not None]


class Hll:
    def __init__(self, p: int = 14) -> None:
        self._s = _sk().HyperLogLog(p)

    @property
    def p(self) -> int:
        return int(self._s.p)

    def update_hash(self, h: int) -> None:
        self._s.update_hashed(h)

    def update_array(self, array: Any) -> None:
        for h in _valid_hashes(array):
            self._s.update_hashed(h)

    def update_hashes(self, hashes: Any) -> None:
        for h in pa.array(hashes).to_pylist():
            if h is not None:
                self._s.update_hashed(h)

    def merge(self, other: Hll) -> None:
        self._s.merge(other._s)

    def estimate(self) -> float:
        return float(self._s.estimate())

    def registers(self) -> bytes:
        return bytes(self._s.registers)

    @staticmethod
    def from_registers(p: int, registers: bytes) -> Hll:
        out = Hll(p)
        if len(registers) != len(out._s.registers):
            raise ValueError("register count does not match p")
        out._s.registers = list(registers)
        return out


class Kll:
    def __init__(self, k: int = 200) -> None:
        if k < 8:
            raise ValueError("k must be >= 8")
        self._s = _sk().KLL(k)

    @property
    def k(self) -> int:
        return int(self._s.k)

    @property
    def n(self) -> int:
        return int(self._s.n)

    def update(self, x: float) -> None:
        self._s.update(x)

    def update_values(self, values: Any) -> None:
        arr = pa.array(values)
        if arr.type != pa.float64():
            raise ValueError("update_values needs a float64 array")
        for x in arr.to_pylist():
            if x is not None and not np.isnan(x):
                self._s.update(x)

    def merge(self, other: Kll) -> None:
        self._s.merge(other._s)

    def quantile(self, q: float) -> float | None:
        return self._s.quantile(q)  # type: ignore[no-any-return]

    def levels(self) -> list[list[float]]:
        return [list(v) for v in self._s.levels]


class SpaceSaving:
    def __init__(self, capacity: int = 64) -> None:
        if capacity == 0:
            raise ValueError("capacity must be >= 1")
        self._s = _sk().SpaceSaving(capacity)

    @property
    def capacity(self) -> int:
        return int(self._s.capacity)

    @property
    def n(self) -> int:
        return int(self._s.n)

    def __len__(self) -> int:
        return len(self._s.counts)

    def update(self, key: int, n: int = 1) -> None:
        self._s.update(key, n)

    def update_array(self, array: Any) -> None:
        for h in _valid_hashes(array):
            self._s.update(h)

    def update_keys(self, keys: Any) -> None:
        for k in pa.array(keys).to_pylist():
            if k is not None:
                self._s.update(k)

    def merge(self, other: SpaceSaving) -> None:
        self._s.merge(other._s)

    def top(self) -> list[tuple[int, int, int]]:
        return [(int(k), c, e) for k, c, e in self._s.top(self._s.capacity)]
