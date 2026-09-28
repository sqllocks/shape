"""Explainable Shape drift scoring."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class Drift:
    path: str
    score: float
    before: object
    after: object
    reason: str


def _rel(a, b):
    if a is None or b is None:
        return 1.0 if a != b else 0.0
    try:
        a = float(a)
        b = float(b)
        if not (isfinite(a) and isfinite(b)):
            return 0.0 if a == b else 1.0
        return abs(b - a) / max(abs(a), abs(b), 1e-12)
    except Exception:
        return 0.0 if a == b else 1.0


def compare(before: dict, after: dict) -> list[Drift]:
    out = []
    bcols = before.get("columns", {})
    acols = after.get("columns", {})
    for name in sorted(set(bcols) | set(acols)):
        if name not in bcols:
            out.append(Drift(f"columns.{name}", 1, None, acols[name], "column added"))
            continue
        if name not in acols:
            out.append(Drift(f"columns.{name}", 1, bcols[name], None, "column removed"))
            continue
        b, a = bcols[name], acols[name]
        if b.get("kind") != a.get("kind"):
            out.append(
                Drift(
                    f"columns.{name}.kind", 1, b.get("kind"), a.get("kind"), "type family changed"
                )
            )
        for metric in ("null_count", "distinct_estimate", "mean", "min", "max", "q50"):
            if metric in b or metric in a:
                s = _rel(b.get(metric), a.get(metric))
                if s:
                    out.append(
                        Drift(
                            f"columns.{name}.{metric}",
                            min(1.0, s),
                            b.get(metric),
                            a.get(metric),
                            "relative metric change",
                        )
                    )
    return sorted(out, key=lambda x: (-x.score, x.path))
