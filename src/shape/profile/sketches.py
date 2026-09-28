"""Bounded-memory mergeable sketches. Pure-Python reference implementations."""

from __future__ import annotations

import hashlib
import heapq
import math
from dataclasses import dataclass, field
from typing import Any


def _h64(v: Any) -> int:
    # Stable cross-process hash. Avoid repr(tuple(...)) allocations for common scalar types.
    if isinstance(v, str):
        b = b"s:" + v.encode("utf-8", "surrogatepass")
    elif isinstance(v, int) and not isinstance(v, bool):
        b = b"i:" + str(v).encode("ascii")
    elif isinstance(v, float):
        b = b"f:" + v.hex().encode("ascii")
    elif v is None:
        b = b"n:"
    else:
        b = (type(v).__name__ + ":" + repr(v)).encode("utf-8", "surrogatepass")
    return int.from_bytes(hashlib.blake2b(b, digest_size=8, person=b"ShapeHLL").digest(), "big")


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
        m = 1 << self.p
        a = 0.7213 / (1 + 1.079 / m)
        z = sum(2.0 ** (-r) for r in self.registers)
        e = a * m * m / z
        v = self.registers.count(0)
        if e <= 2.5 * m and v:
            e = m * math.log(m / v)
        return e

    def state(self):
        return {"algorithm": "hll", "p": self.p, "registers": self.registers}


@dataclass
class SpaceSaving:
    capacity: int = 64
    counts: dict[Any, tuple[int, int]] = field(default_factory=dict)
    _heap: list = field(default_factory=list, repr=False)
    _seq: int = 0

    def _push(self, v, c):
        self._seq += 1
        # repr tie-break avoids comparing unlike value types.
        heapq.heappush(self._heap, (c, self._seq, v))

    def update(self, v: Any, n: int = 1):
        cur = self.counts.get(v)
        if cur is not None:
            c, e = cur
            c += n
            self.counts[v] = (c, e)
            self._push(v, c)
            return
        if len(self.counts) < self.capacity:
            self.counts[v] = (n, 0)
            self._push(v, n)
            return
        while self._heap:
            c, _, victim = heapq.heappop(self._heap)
            current = self.counts.get(victim)
            if current is not None and current[0] == c:
                break
        else:
            victim, (c, _) = min(self.counts.items(), key=lambda kv: kv[1][0])
        del self.counts[victim]
        self.counts[v] = (c + n, c)
        self._push(v, c + n)

    def merge(self, o: SpaceSaving):
        if self.capacity != o.capacity:
            raise ValueError("incompatible SpaceSaving capacity")
        for v, (c, _) in o.counts.items():
            self.update(v, c)
        return self

    def top(self, k: int = 10):
        return sorted(
            ((v, c, e) for v, (c, e) in self.counts.items()), key=lambda x: (-x[1], repr(x[0]))
        )[:k]


@dataclass
class KLL:
    """Compact mergeable quantile sketch with deterministic compaction.
    Reference KLL-family implementation; serialized algorithm version prevents ambiguity.
    """

    k: int = 200
    levels: list[list[float]] = field(default_factory=lambda: [[]])
    n: int = 0
    _compactions: int = 0

    def update(self, x: float):
        self.levels[0].append(float(x))
        self.n += 1
        self._compact(0)

    def _compact(self, level: int):
        while level < len(self.levels) and len(self.levels[level]) > self.k:
            vals = sorted(self.levels[level])
            parity = (self._compactions + level) & 1
            self._compactions += 1
            promoted = vals[parity::2]
            self.levels[level] = []
            if level + 1 == len(self.levels):
                self.levels.append([])
            self.levels[level + 1].extend(promoted)
            level += 1

    def merge(self, o: KLL):
        if self.k != o.k:
            raise ValueError("incompatible KLL k")
        while len(self.levels) < len(o.levels):
            self.levels.append([])
        for i, v in enumerate(o.levels):
            self.levels[i].extend(v)
            self._compact(i)
        self.n += o.n
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
        return {"algorithm": "kll-reference-v1", "k": self.k, "n": self.n, "levels": self.levels}
