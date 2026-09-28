"""High-throughput streaming microbatch engine.
Columnar microbatches preserve streaming/backpressure boundaries while avoiding
Python per-event overhead.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BatchCheckpoint:
    rows: int
    batches: int
    token: str | None = None


class VectorStreamProfiler:
    def __init__(self):
        self.rows = 0
        self.batches = 0
        self.last_shape = None

    def add_batch(self, columns):
        from shape.capture import capture_columns

        s = capture_columns(columns)
        self.rows += s["rows"]
        self.batches += 1
        self.last_shape = s
        return s

    def checkpoint(self, token=None):
        return BatchCheckpoint(self.rows, self.batches, token)


def deduplicate_ids(ids, seen: set):
    """
    Reference exact dedupe for bounded/test streams; distributed production dedupe belongs in
    engine state.
    """
    import numpy as np

    a = np.asarray(ids)
    keep = np.empty(len(a), dtype=bool)
    for i, x in enumerate(a):
        v = x.item() if hasattr(x, "item") else x
        keep[i] = v not in seen
        if keep[i]:
            seen.add(v)
    return keep
