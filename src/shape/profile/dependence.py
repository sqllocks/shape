"""Bounded nonlinear dependence evidence via discretized mutual information."""

from __future__ import annotations

import math
import random
from collections import Counter


def _reservoir(pairs, max_rows):
    """A uniform sample of at most ``max_rows`` items from a stream, in O(max_rows) memory
    (algorithm R with a fixed seed, so the result is reproducible). Inputs no larger than
    ``max_rows`` are kept whole and in order."""
    rnd = random.Random(0x5EED)
    kept = []
    seen = 0
    for p in pairs:
        seen += 1
        if len(kept) < max_rows:
            kept.append(p)
        else:
            j = rnd.randrange(seen)
            if j < max_rows:
                kept[j] = p
    return kept, seen


def normalized_mutual_information(rows, left, right, bins=16, max_rows=100_000):
    """Normalized mutual information of two columns over discretized values.

    Memory is bounded: at most ``max_rows`` non-null pairs are retained (a uniform random
    sample when the input is larger), so for larger inputs the value is an estimate."""
    pairs, _ = _reservoir(
        (
            (r.get(left), r.get(right))
            for r in rows
            if r.get(left) is not None and r.get(right) is not None
        ),
        max_rows,
    )
    if not pairs:
        return 0.0

    def encode(vals):
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
            s = sorted(float(v) for v in vals)
            n = len(s)
            cuts = [s[min(n - 1, int(n * i / bins))] for i in range(1, bins)]
            import bisect

            return [bisect.bisect_right(cuts, float(v)) for v in vals]
        return [str(v) for v in vals]

    xs = encode([x for x, _ in pairs])
    ys = encode([y for _, y in pairs])
    n = len(xs)
    cx = Counter(xs)
    cy = Counter(ys)
    cxy = Counter(zip(xs, ys, strict=False))
    mi = 0.0
    for (x, y), c in cxy.items():
        p = c / n
        mi += p * math.log(p / ((cx[x] / n) * (cy[y] / n)))
    hx = -sum((c / n) * math.log(c / n) for c in cx.values())
    hy = -sum((c / n) * math.log(c / n) for c in cy.values())
    return mi / max(min(hx, hy), 1e-12)


def conditional_numeric_means(rows, category, value, min_count=20, max_groups=10_000):
    """Mean of ``value`` per category. At most ``max_groups`` categories are tracked (the first
    seen); rows of later categories are ignored, so memory does not grow with cardinality."""
    groups = {}
    for r in rows:
        c = r.get(category)
        v = r.get(value)
        if c is None or not isinstance(v, (int, float)):
            continue
        if c not in groups and len(groups) >= max_groups:
            continue
        s, n = groups.get(c, (0.0, 0))
        groups[c] = (s + float(v), n + 1)
    return {str(k): s / n for k, (s, n) in groups.items() if n >= min_count}
