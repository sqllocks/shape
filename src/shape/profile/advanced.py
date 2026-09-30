"""Additional bounded/explainable profiling evidence."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import log2, sqrt


@dataclass(frozen=True, slots=True)
class MissingnessEvidence:
    left: str
    right: str
    rows: int
    both_missing: int
    phi: float


def missingness_dependency(rows, left, right):
    a = b = c = d = 0
    for r in rows:
        x = r.get(left) is None
        y = r.get(right) is None
        if x and y:
            a += 1
        elif x:
            b += 1
        elif y:
            c += 1
        else:
            d += 1
    n = a + b + c + d
    den = sqrt((a + b) * (c + d) * (a + c) * (b + d))
    return MissingnessEvidence(left, right, n, a, ((a * d - b * c) / den if den else 0.0))


def entropy(values):
    c = Counter(v for v in values if v is not None)
    n = sum(c.values())
    return 0.0 if not n else -sum((v / n) * log2(v / n) for v in c.values())


def infer_pattern(values, sample_limit=1000):
    def pat(s):
        out = []
        last = None
        for ch in str(s):
            k = "D" if ch.isdigit() else "A" if ch.isalpha() else ch
            if k != last:
                out.append(k)
                last = k
        return "".join(out)

    c = Counter()
    n = 0
    for v in values:
        if v is None:
            continue
        c[pat(v)] += 1
        n += 1
        if n >= sample_limit:
            break
    return c.most_common(10)


def pearson(rows, left, right):
    """Pearson correlation of two numeric columns in one pass with O(1) memory (Welford-style
    co-moments); the rows are never retained."""
    n = 0
    mx = my = sxx = syy = sxy = 0.0
    for r in rows:
        x, y = r.get(left), r.get(right)
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            continue
        n += 1
        dx = x - mx
        mx += dx / n
        dy = y - my
        my += dy / n
        sxx += dx * (x - mx)
        syy += dy * (y - my)
        sxy += dx * (y - my)
    if n < 2:
        return 0.0
    return sxy / sqrt(sxx * syy) if sxx and syy else 0.0
