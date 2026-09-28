"""Interpolate simple numeric Shape evidence across time."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ShapePoint:
    at: float
    shape: dict


def interpolate(a: ShapePoint, b: ShapePoint, t: float) -> dict:
    if not a.at <= t <= b.at or b.at == a.at:
        raise ValueError("t outside interval or zero interval")
    w = (t - a.at) / (b.at - a.at)
    out = {"columns": {}}
    for k in set(a.shape.get("columns", {})) | set(b.shape.get("columns", {})):
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
