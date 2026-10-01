"""Distinct counting and heavy hitters over Python values, on the kernel's sketches.

The kernel's ``Hll`` and ``SpaceSaving`` work on canonical 64-bit hashes (T-13). These two small
classes put the value interface streaming evidence and the key checks need on top of them:
``update(value)`` hashes the value canonically (``1`` and ``1.0`` are one value; ``None`` and NaN
are skipped, P7), and ``TopValues`` remembers a representative value per tracked hash.
"""

from __future__ import annotations

from typing import Any

from .hashing import hash_value
from .sketches import Hll, SpaceSaving


class DistinctCounter:
    """HyperLogLog (p=14) of values: ``update``, ``merge``, ``estimate``."""

    def __init__(self, p: int = 14) -> None:
        self._h = Hll(p)

    def update(self, v: Any) -> None:
        h = hash_value(v)
        if h is not None:
            self._h.update_hash(h)

    def merge(self, other: DistinctCounter) -> None:
        self._h.merge(other._h)

    def estimate(self) -> float:
        return float(self._h.estimate())


class TopValues:
    """SpaceSaving (capacity 64) of values: ``update``, ``merge``, ``top`` as
    ``[value, count, error]`` by count, largest first."""

    def __init__(self, capacity: int = 64) -> None:
        self._s = SpaceSaving(capacity)
        self._capacity = capacity
        self._values: dict[int, Any] = {}

    def update(self, v: Any, n: int = 1) -> None:
        h = hash_value(v)
        if h is None:
            return
        self._s.update(h, n)
        self._values.setdefault(h, v)
        if len(self._values) > 4 * self._capacity:
            self._prune()

    def _prune(self) -> None:
        live = {key for key, _, _ in self._s.top()}
        self._values = {k: v for k, v in self._values.items() if k in live}

    def merge(self, other: TopValues) -> None:
        self._s.merge(other._s)
        for k, v in other._values.items():
            self._values.setdefault(k, v)
        self._prune()

    def top(self, k: int = 10) -> list[list[Any]]:
        return [[self._values[key], count, err] for key, count, err in self._s.top()[:k]]
