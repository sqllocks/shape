"""Additional bounded/explainable profiling evidence."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import isfinite, log2, sqrt
from numbers import Real
from typing import Any


@dataclass(frozen=True, slots=True)
class MissingnessEvidence:
    left: str
    right: str
    rows: int
    both_missing: int
    phi: float


def missingness_dependency(
    rows: Iterable[Mapping[str, Any]], left: str, right: str
) -> MissingnessEvidence:
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


def entropy(values: Iterable[Any]) -> float:
    c = Counter(v for v in values if v is not None)
    n = sum(c.values())
    return 0.0 if not n else -sum((v / n) * log2(v / n) for v in c.values())


def infer_pattern(values: Iterable[Any], sample_limit: int = 1000) -> list[tuple[str, int]]:
    def pat(s: Any) -> str:
        out: list[str] = []
        last = None
        for ch in str(s):
            k = "D" if ch.isdigit() else "A" if ch.isalpha() else ch
            if k != last:
                out.append(k)
                last = k
        return "".join(out)

    c: Counter[str] = Counter()
    n = 0
    for v in values:
        if v is None:
            continue
        c[pat(v)] += 1
        n += 1
        if n >= sample_limit:
            break
    return c.most_common(10)


def finite_number(v: Any) -> float | None:
    """``v`` as a float when it is a finite real number (numpy numbers too; a bool counts as
    0 or 1), else None: a missing value, NaN, infinity or anything that is not a number (#315)."""
    if not isinstance(v, Real):
        return None
    f = float(v)
    return f if isfinite(f) else None


def pearson(rows: Iterable[Mapping[str, Any]], left: str, right: str) -> float:
    """Pearson correlation of two numeric columns in one pass with O(1) memory (Welford-style
    co-moments); the rows are never retained. Rows where either value is missing, not finite
    or not a number are skipped."""
    n = 0
    mx = my = sxx = syy = sxy = 0.0
    for r in rows:
        x, y = finite_number(r.get(left)), finite_number(r.get(right))
        if x is None or y is None:
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
