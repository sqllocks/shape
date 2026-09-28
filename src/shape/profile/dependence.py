"""Bounded nonlinear dependence evidence via discretized mutual information."""

from __future__ import annotations

import math
from collections import Counter


def normalized_mutual_information(rows, left, right, bins=16):
    pairs = [
        (r.get(left), r.get(right))
        for r in rows
        if r.get(left) is not None and r.get(right) is not None
    ]
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


def conditional_numeric_means(rows, category, value, min_count=20):
    groups = {}
    for r in rows:
        c = r.get(category)
        v = r.get(value)
        if c is None or not isinstance(v, (int, float)):
            continue
        s, n = groups.get(c, (0.0, 0))
        groups[c] = (s + float(v), n + 1)
    return {str(k): s / n for k, (s, n) in groups.items() if n >= min_count}
