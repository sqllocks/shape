"""Cross-cutting correctness invariants."""

from __future__ import annotations

import math


def referential_integrity(parents, children, parent_key, child_fk):
    keys = {r.get(parent_key) for r in parents}
    bad = [i for i, r in enumerate(children) if r.get(child_fk) not in keys]
    return {"passed": not bad, "violating_rows": tuple(bad), "checked": len(children)}


def uniqueness(rows, fields):
    seen = set()
    bad = []
    for i, r in enumerate(rows):
        k = tuple(r.get(f) for f in fields)
        if k in seen:
            bad.append(i)
        seen.add(k)
    return {"passed": not bad, "violating_rows": tuple(bad), "checked": len(rows)}


def deterministic_partitions(plan, count, partitions):
    expected = list(plan.rows(count))
    # GenerationPlan is index-derived; verify arbitrary partition boundaries by reconstructing
    # exact indices.
    actual = []
    for start, end in partitions:
        for i in range(start, end):
            actual.append(next(plan.rows_at((i,))))
    return expected == actual


def finite_shape(shape):
    def walk(x):
        if isinstance(x, float):
            return math.isfinite(x)
        if isinstance(x, dict):
            return all(walk(v) for v in x.values())
        if isinstance(x, (list, tuple)):
            return all(walk(v) for v in x)
        return True

    return walk(shape)
