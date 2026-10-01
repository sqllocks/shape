"""Temporal sequence evidence."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def lag_autocorrelation(values: Iterable[Any], lag: int = 1) -> float | None:
    xs = [float(x) for x in values if x is not None]
    if lag < 1 or len(xs) <= lag:
        return None
    m = sum(xs) / len(xs)
    den = sum((x - m) ** 2 for x in xs)
    if den == 0:
        return 1.0
    return sum((xs[i] - m) * (xs[i - lag] - m) for i in range(lag, len(xs))) / den
