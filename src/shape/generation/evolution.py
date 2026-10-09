"""Interpolate simple numeric Shape evidence across time."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ShapePoint:
    at: float
    shape: dict


def interpolate(a: ShapePoint, b: ShapePoint, t: float) -> dict:
    """Interpolate column evidence between two dated Shape points."""
    if not a.at <= t <= b.at or b.at == a.at:
        raise ValueError("t outside interval or zero interval")
    w = (t - a.at) / (b.at - a.at)
    out = {"columns": {}}
    # the first shape's columns in order, then the second's new ones: never a set, whose order
    # changes from process to process and with it every value drawn after it (#652)
    for k in dict.fromkeys([*a.shape.get("columns", {}), *b.shape.get("columns", {})]):
        x = a.shape.get("columns", {}).get(k)
        y = b.shape.get("columns", {}).get(k)
        if x is None:
            out["columns"][k] = y
            continue
        if y is None:
            out["columns"][k] = x
            continue
        z = dict(x)
        for m in ("mean", "min", "max", "q50", "null_count", "distinct_estimate"):
            if isinstance(x.get(m), (int, float)) and isinstance(y.get(m), (int, float)):
                z[m] = x[m] + w * (y[m] - x[m])
        out["columns"][k] = z
    return out
