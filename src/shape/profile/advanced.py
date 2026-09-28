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
    vals = [
        (r.get(left), r.get(right))
        for r in rows
        if isinstance(r.get(left), (int, float)) and isinstance(r.get(right), (int, float))
    ]
    if len(vals) < 2:
        return 0.0
    xs = [x for x, _ in vals]
    ys = [y for _, y in vals]
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    num = sum((x - mx) * (y - my) for x, y in vals)
    dx = sum((x - mx) ** 2 for x in xs)
    dy = sum((y - my) ** 2 for y in ys)
    return num / sqrt(dx * dy) if dx and dy else 0.0
