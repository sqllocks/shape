"""Relational fidelity evidence."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RelationalFidelity:
    parent_rows: int
    child_rows: int
    orphan_rows: int
    parent_coverage: float
    mean_children: float
    passed: bool


def relational_fidelity(parents, children, parent_key, child_fk, max_orphan_rate=0.0):
    keys = {p.get(parent_key) for p in parents}
    counts = {k: 0 for k in keys}
    orphans = 0
    for c in children:
        k = c.get(child_fk)
        if k not in keys:
            orphans += 1
        else:
            counts[k] += 1
    coverage = (sum(v > 0 for v in counts.values()) / len(keys)) if keys else 1.0
    mean = (sum(counts.values()) / len(keys)) if keys else 0.0
    rate = orphans / max(len(children), 1)
    return RelationalFidelity(
        len(parents), len(children), orphans, coverage, mean, rate <= max_orphan_rate
    )
