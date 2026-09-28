"""High-throughput columnar generation kernels.
These kernels are deterministic per batch seed and structurally preserve key integrity."""

from __future__ import annotations


def composite_keys(n: int, start: int = 0, prefix: str = "C"):
    import numpy as np

    i = np.arange(start, start + n, dtype=np.int64)
    # Numeric components stay zero-copy-ish; formatted string component is optional/expensive.
    return {"entity_id": i, "partition_id": i % 1024}


def foreign_keys(n: int, parent_count: int, seed: int = 0):
    if parent_count < 1:
        raise ValueError("parent_count")
    import numpy as np

    rng = np.random.default_rng(seed)
    return rng.integers(0, parent_count, size=n, dtype=np.int64)


def parent_child_keys(parent_count: int, children_per_parent: int):
    import numpy as np

    if parent_count < 0 or children_per_parent < 0:
        raise ValueError("counts")
    parents = np.repeat(np.arange(parent_count, dtype=np.int64), children_per_parent)
    ordinals = np.tile(np.arange(1, children_per_parent + 1, dtype=np.int32), parent_count)
    return parents, ordinals
