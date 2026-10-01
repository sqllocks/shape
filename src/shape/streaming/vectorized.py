"""High-throughput streaming microbatch engine.
Columnar microbatches preserve streaming/backpressure boundaries while avoiding
Python per-event overhead.
"""

from __future__ import annotations

from collections.abc import Iterable, MutableSet
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True, slots=True)
class BatchCheckpoint:
    rows: int
    batches: int
    token: str | None = None


class VectorStreamProfiler:
    def __init__(self) -> None:
        self.rows = 0
        self.batches = 0
        self.last_shape: dict[str, Any] | None = None

    def add_batch(self, columns: Any) -> dict[str, Any]:
        from shape.capture import capture_columns

        s = capture_columns(columns)
        self.rows += s["rows"]
        self.batches += 1
        self.last_shape = s
        return s

    def checkpoint(self, token: str | None = None) -> BatchCheckpoint:
        return BatchCheckpoint(self.rows, self.batches, token)


def deduplicate_ids(ids: Iterable[Any], seen: MutableSet[Any]) -> np.ndarray:
    """Keep-mask for a batch against a set of ids seen so far (``seen`` is updated).

    Equal ids in the batch collapse to their first row with one ``np.unique``, and ``seen`` is
    asked once per distinct id, not once per row (S5). For a window that is bounded by size and
    time, and a batch that costs a few array operations, use ``Deduplicator``.
    """
    a = np.asarray(ids)
    keep = np.zeros(len(a), dtype=bool)
    try:
        unique, first = np.unique(a, return_index=True)
    except TypeError:  # unorderable mixed types: fall back to one dict pass
        firsts: dict[Any, int] = {}
        for i, x in enumerate(a.tolist()):
            firsts.setdefault(x, i)
        unique, first = np.asarray(list(firsts), dtype=object), np.asarray(list(firsts.values()))
    for value, row in zip(unique.tolist(), first.tolist(), strict=True):
        if value not in seen:
            seen.add(value)
            keep[row] = True
    return keep
