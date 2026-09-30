"""Bounded-memory mergeable sketches. Pure-Python reference implementations."""

from __future__ import annotations

import base64
import math
from dataclasses import dataclass, field
from typing import Any

from shape.kernel.hashing import hash_value


def _h64(v: Any) -> int | None:
    """Canonical seeded XXH3-64 (T-13): ``1`` and ``1.0`` agree; null and NaN give ``None``."""
    return hash_value(v)


def _excluded(v: Any) -> bool:
    """Null and NaN are never counted by the sketches (T-13)."""
    return v is None or (isinstance(v, float) and v != v)


_ALPHA_INF = 1.0 / (2.0 * math.log(2.0))


def _sigma(x: float) -> float:
    if x == 1.0:
        return math.inf
    y, z = 1.0, x
    while True:
        x *= x
        z_old = z
        z += x * y
        y += y
        if z == z_old:
            return z


def _tau(x: float) -> float:
    if x == 0.0 or x == 1.0:
        return 0.0
    y, z = 1.0, 1.0 - x
    while True:
        x = math.sqrt(x)
        z_old = z
        y *= 0.5
        z -= (1.0 - x) ** 2 * y
        if z == z_old:
            return z / 3.0


@dataclass
class HyperLogLog:
    p: int = 14
    registers: list[int] = field(default_factory=list)

    def __post_init__(self):
        if not 4 <= self.p <= 18:
            raise ValueError("p must be 4..18")
        if not self.registers:
            self.registers = [0] * (1 << self.p)

    def update(self, v: Any):
        x = _h64(v)
        if x is not None:
            self.update_hashed(x)

    def update_hashed(self, x: int):
        """Add an already-computed 64-bit hash."""
        idx = x >> (64 - self.p)
        rem = (x << self.p) & ((1 << 64) - 1)
        bits = 64 - self.p
        rank = (bits + 1) if rem == 0 else (64 - rem.bit_length() + 1)
        if rank > self.registers[idx]:
            self.registers[idx] = rank

    def merge(self, o: HyperLogLog):
        if self.p != o.p:
            raise ValueError("incompatible HLL precision")
        self.registers = [max(a, b) for a, b in zip(self.registers, o.registers, strict=False)]
        return self

    def estimate(self) -> float:
        """Cardinality estimate: Ertl's improved estimator (2017). It needs no empirical bias
        tables and is unbiased across the whole range (small counts, the transition, and
        saturation); relative standard error is about 1.04/sqrt(m)."""
        m = 1 << self.p
        q = 64 - self.p
        counts = [0] * (q + 2)
        for r in self.registers:
            counts[r] += 1
        z = m * _tau(1.0 - counts[q + 1] / m)
        for k in range(q, 0, -1):
            z = 0.5 * (z + counts[k])
        z += m * _sigma(counts[0] / m)
        return _ALPHA_INF * m * m / z

    def state(self):
        return {
            "algorithm": "hll",
            "estimator": "ertl-improved",
            "p": self.p,
            "registers": base64.b64encode(bytes(self.registers)).decode("ascii"),
        }


def _key_order(v: Any) -> tuple[int, Any]:
    """Deterministic total order on keys (ints numerically, everything else by repr)."""
    return (0, v) if isinstance(v, int) and not isinstance(v, bool) else (1, repr(v))


@dataclass
class SpaceSaving:
    """Metwally et al. SpaceSaving with ``capacity`` counters (T-14). Memory is bounded.

    ``counts[key] = (count, error)``: ``count - error <= true count <= count`` and
    ``error <= n / capacity``. The victim on a miss is the entry with the smallest count, ties
    broken by least recently updated. Merging follows Agarwal et al. (mergeable summaries): a
    key missing from a full summary is credited with that summary's minimum count, error
    terms add, and the result keeps the ``capacity`` largest counts (ties by key order), so
    ``merge`` is symmetric.
    """

    capacity: int = 64
    counts: dict[Any, tuple[int, int]] = field(default_factory=dict)
    _seq: dict[Any, int] = field(default_factory=dict, repr=False)
    _clock: int = 0
    n: int = 0

    def update(self, v: Any, n: int = 1):
        if _excluded(v):
            return
        self.n += n
        self._clock += 1
        cur = self.counts.get(v)
        if cur is not None:
            self.counts[v] = (cur[0] + n, cur[1])
            self._seq[v] = self._clock
            return
        if len(self.counts) < self.capacity:
            self.counts[v] = (n, 0)
            self._seq[v] = self._clock
            return
        victim = min(self.counts, key=lambda k: (self.counts[k][0], self._seq[k]))
        c = self.counts.pop(victim)[0]
        del self._seq[victim]
        self.counts[v] = (c + n, c)
        self._seq[v] = self._clock

    def _min_count(self) -> int:
        full = len(self.counts) >= self.capacity
        return min((c for c, _ in self.counts.values()), default=0) if full else 0

    def merge(self, o: SpaceSaving):
        if self.capacity != o.capacity:
            raise ValueError("incompatible SpaceSaving capacity")
        m1, m2 = self._min_count(), o._min_count()
        merged: dict[Any, tuple[int, int]] = {}
        for k in self.counts.keys() | o.counts.keys():
            c1, e1 = self.counts.get(k, (m1, m1))
            c2, e2 = o.counts.get(k, (m2, m2))
            merged[k] = (c1 + c2, e1 + e2)
        keep = sorted(merged, key=lambda k: (-merged[k][0], _key_order(k)))[: self.capacity]
        self.counts = {k: merged[k] for k in keep}
        self.n += o.n
        # recency only matters for tie-breaking later: order the survivors deterministically
        self._seq = {k: i for i, k in enumerate(sorted(keep, key=_key_order))}
        self._clock = len(self._seq)
        return self

    def top(self, k: int = 10):
        return sorted(
            ((v, c, e) for v, (c, e) in self.counts.items()),
            key=lambda x: (-x[1], _key_order(x[0])),
        )[:k]

    def state(self):
        return {
            "algorithm": "space-saving",
            "capacity": self.capacity,
            "n": self.n,
            "entries": [[k, c, e] for k, c, e in self.top(self.capacity)],
        }


def kll_capacity(k: int, level: int, levels: int) -> int:
    """Capacity of ``level`` when there are ``levels`` levels: ``max(2, k * (2/3)^depth)`` with
    depth counted down from the top level, in exact integer arithmetic (identical in Rust)."""
    depth = levels - 1 - level
    if depth > 40:
        return 2
    return max(2, (k << depth) // 3**depth)


@dataclass
class KLL:
    """KLL quantile sketch (Karnin, Lang, Liberty) with deterministic compaction (T-14).

    Level ``l`` holds items of weight ``2**l`` with geometrically decreasing capacities
    (2/3 per level below the top), which gives rank error of about 1.65/k (0.8% at k=200).
    Compaction sorts a level, keeps its smallest item back when the count is odd, and promotes
    every other item of the rest (alternating parity), so total weight is preserved *exactly*
    (``sum(len(level) * 2**l) == n`` always).
    """

    k: int = 200
    levels: list[list[float]] = field(default_factory=lambda: [[]])
    n: int = 0
    _compactions: int = 0

    def _size(self) -> int:
        return sum(len(v) for v in self.levels)

    def _total_capacity(self) -> int:
        h = len(self.levels)
        return sum(kll_capacity(self.k, i, h) for i in range(h))

    def update(self, x: float):
        self.levels[0].append(float(x))
        self.n += 1
        self._compress()

    def _compress(self):
        while self._size() > self._total_capacity():
            h = len(self.levels)
            for i in range(h):
                if len(self.levels[i]) >= kll_capacity(self.k, i, h):
                    self._compact(i)
                    break

    def _compact(self, level: int):
        if level + 1 == len(self.levels):
            self.levels.append([])
        vals = sorted(self.levels[level])
        leftover = [vals.pop(0)] if len(vals) % 2 else []
        parity = self._compactions & 1
        self._compactions += 1
        self.levels[level] = leftover
        self.levels[level + 1].extend(vals[parity::2])

    def merge(self, o: KLL):
        if self.k != o.k:
            raise ValueError("incompatible KLL k")
        while len(self.levels) < len(o.levels):
            self.levels.append([])
        for i, v in enumerate(o.levels):
            self.levels[i].extend(v)
        self.n += o.n
        self._compress()
        return self

    def quantile(self, q: float):
        if not 0 <= q <= 1:
            raise ValueError("q must be 0..1")
        weighted = []
        for level, vals in enumerate(self.levels):
            weighted.extend((v, 1 << level) for v in vals)
        if not weighted:
            return None
        weighted.sort()
        total = sum(w for _, w in weighted)
        target = q * (total - 1)
        acc = 0
        for v, w in weighted:
            if acc + w > target:
                return v
            acc += w
        return weighted[-1][0]

    def state(self):
        return {"algorithm": "kll-v2", "k": self.k, "n": self.n, "levels": self.levels}
