"""Bounded dependency profiling for high-cardinality streams."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class BoundedFDEvidence:
    determinant: tuple[str, ...]
    dependent: str
    rows_seen: int
    rows_sampled: int
    groups: int
    confidence: float
    sampling_rate: float


def _score(key) -> int:
    return int.from_bytes(
        hashlib.blake2b(
            repr(key).encode("utf-8", "surrogatepass"), digest_size=8, person=b"ShapeFD"
        ).digest(),
        "big",
    )


def bounded_functional_dependency(
    rows: Iterable[Mapping[str, Any]],
    determinant: tuple[str, ...],
    dependent: str,
    max_rows: int = 100000,
) -> BoundedFDEvidence:
    if max_rows < 1:
        raise ValueError("max_rows must be positive")
    # retain deterministic lowest hashes; memory bounded by max_rows
    import heapq

    heap = []
    seen = 0
    for r in rows:
        seen += 1
        key = (tuple(r.get(k) for k in determinant), r.get(dependent))
        s = _score((seen, key))
        item = (-s, seen, key)
        if len(heap) < max_rows:
            heapq.heappush(heap, item)
        elif item > heap[0]:
            heapq.heapreplace(heap, item)
    groups = defaultdict(Counter)
    for _, _, (d, v) in heap:
        groups[d][v] += 1
    correct = sum(max(c.values()) for c in groups.values()) if groups else 0
    sampled = len(heap)
    return BoundedFDEvidence(
        determinant,
        dependent,
        seen,
        sampled,
        len(groups),
        correct / sampled if sampled else 0.0,
        sampled / seen if seen else 0.0,
    )
