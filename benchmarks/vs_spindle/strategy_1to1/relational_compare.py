"""T-21 comparison of one observation of a relational case (``relational_cases.py``) against the
baseline's four seeds.

Most observations go through ``compare.check`` (clauses (b)-(e): null rate, KS for numbers and
dates, total variation distance with vocabulary overlap for low-cardinality values). The
distribution observations in ``DISTRIBUTIONS`` (fan-out: children per parent, versions per key, a
position along a table) are counts with a long tail whose exact values differ from seed to seed, so
a vocabulary overlap says nothing about them; they are compared with the KS clause (c) alone, with
the plan's tolerance: ``max(critical value at alpha = 0.001, 1.5 x the baseline's largest
seed-to-seed KS + 0.002)``. That is clause (f), "fan-out within the baseline's seed-to-seed range".
"""

from __future__ import annotations

import itertools
import math
from typing import Any

import compare

DISTRIBUTIONS = frozenset({"fanout", "versions_per_key", "parent_position"})


def _ks(shape: dict[str, Any], baseline: list[dict[str, Any]]) -> list[str]:
    qs = [b["quantiles"] for b in baseline]
    drift = max((compare.ks_grids(a, b) for a, b in itertools.combinations(qs, 2)), default=0.0)
    n_base = sum(b["n"] - b["null_count"] for b in baseline) / len(baseline)
    n_shape = shape["n"] - shape["null_count"]
    tol = max(compare.CRIT_001 * math.sqrt(1.0 / n_shape + 1.0 / n_base), 1.5 * drift + 0.002)
    got = max(compare.ks_grids(shape["quantiles"], q) for q in qs)
    return [f"KS {got:.5f} > {tol:.5f}"] if got > tol else []


def check(name: str, shape: dict[str, Any], baseline: list[dict[str, Any]]) -> list[str]:
    """The failures of one observation (empty when it is equivalent)."""
    empty = [b["n"] - b["null_count"] == 0 for b in baseline]
    if all(empty) or shape["n"] - shape["null_count"] == 0:
        # nothing to compare is only equivalent when it is nothing on both sides
        return [] if all(empty) and shape["n"] - shape["null_count"] == 0 else ["empty on one side"]
    if any(empty):
        return ["empty in some baseline seeds"]
    if name in DISTRIBUTIONS:
        return _ks(shape, baseline)
    return compare.check(shape, baseline)
