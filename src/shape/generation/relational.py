"""Relational and temporal generation primitives."""

from __future__ import annotations

import random
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


@dataclass(frozen=True, slots=True)
class ParentChildSpec:
    parent_key: str
    child_fk: str
    min_children: int = 0
    max_children: int = 5

    def __post_init__(self):
        if self.min_children < 0 or self.max_children < self.min_children:
            raise ValueError("invalid child bounds")


def generate_children(parents: Iterable[Mapping[str, Any]], spec: ParentChildSpec, seed: int = 0):
    for i, p in enumerate(parents):
        rng = random.Random((seed << 64) ^ i)
        n = rng.randint(spec.min_children, spec.max_children)
        for j in range(n):
            yield {spec.child_fk: p[spec.parent_key], "child_ordinal": j + 1}


def scd2_versions(entity_id: Any, start: datetime, changes: int, interval: timedelta):
    if changes < 1:
        raise ValueError("changes must be >=1")
    for i in range(changes):
        valid_from = start + i * interval
        valid_to = None if i == changes - 1 else start + (i + 1) * interval
        yield {
            "entity_id": entity_id,
            "version": i + 1,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "is_current": i == changes - 1,
        }


def generate_composite_keys(n: int, parts: int = 2, start: int = 0):
    """Vectorized deterministic composite integer keys."""
    import numpy as np

    if n < 0 or parts < 1:
        raise ValueError("invalid key dimensions")
    base = np.arange(start, start + n, dtype=np.int64)
    return tuple(base if j == 0 else (base * (j + 1) + j) for j in range(parts))


def generate_fk_indices(parent_count: int, child_count: int, seed: int = 0, skew: float = 0.0):
    """Vectorized valid FK row indices. skew=0 is uniform; positive skew uses Zipf-like ranks."""
    import numpy as np

    if parent_count < 1 or child_count < 0:
        raise ValueError("invalid relationship size")
    rng = np.random.default_rng(seed)
    if skew <= 0:
        return rng.integers(0, parent_count, size=child_count, dtype=np.int64)
    # Closed-form inverse power-law sampler: O(children), avoiding a parent-sized CDF and binary
    # search.
    a = float(skew)
    u = rng.random(child_count)
    top = float(parent_count) + 1.0  # x in [1, parent_count + 1): every parent can be drawn
    if abs(a - 1.0) < 1e-12:
        x = np.exp(u * np.log(top))
    else:
        q = 1.0 - a
        x = (1.0 + u * (top**q - 1.0)) ** (1.0 / q)
    return np.minimum(parent_count - 1, np.maximum(0, x.astype(np.int64) - 1))


def materialize_composite_fks(parent_key_parts, indices):
    """Gather all parts of a composite parent key using one FK index vector."""
    import numpy as np

    idx = np.asarray(indices, dtype=np.int64)
    return tuple(np.asarray(part)[idx] for part in parent_key_parts)
