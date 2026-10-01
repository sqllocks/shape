"""Explainable Shape drift scoring."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any

from shape.spec.view import columns_of, family, median, model_of


@dataclass(frozen=True, slots=True)
class Drift:
    path: str
    score: float
    before: object
    after: object
    reason: str


def _rel(a: Any, b: Any) -> float:
    if a is None or b is None:
        return 1.0 if a != b else 0.0
    try:
        x, y = float(a), float(b)
        if not (isfinite(x) and isfinite(y)):
            return 0.0 if x == y else 1.0
        return abs(y - x) / max(abs(x), abs(y), 1e-12)
    except Exception:
        return 0.0 if a == b else 1.0


_METRICS = ("null_count", "distinct", "mean", "min", "max")


def _metric(column: dict[str, Any], name: str) -> Any:
    return median(column) if name == "median" else column.get(name)


def compare(before: Any, after: Any) -> list[Drift]:
    """Drift between two Shapes (v2 models, or v1 captures that are migrated), most severe
    first. A table is compared by column name; with several tables the paths are
    ``tables.<table>.columns.<column>...``."""
    b_model, a_model = model_of(before), model_of(after)
    single = len(b_model["tables"]) == 1 and len(a_model["tables"]) == 1
    out: list[Drift] = []
    for tname in sorted(set(b_model["tables"]) | set(a_model["tables"])):
        prefix = "" if single else f"tables.{tname}."
        if tname not in a_model["tables"] or tname not in b_model["tables"]:
            gone = tname not in a_model["tables"]
            out.append(
                Drift(
                    f"tables.{tname}",
                    1,
                    None if not gone else b_model["tables"][tname]["name"],
                    None if gone else a_model["tables"][tname]["name"],
                    "table removed" if gone else "table added",
                )
            )
            continue
        bcols = columns_of(b_model["tables"][tname])
        acols = columns_of(a_model["tables"][tname])
        out += _compare_columns(bcols, acols, prefix)
    return sorted(out, key=lambda x: (-x.score, x.path))


def _compare_columns(
    bcols: dict[str, dict[str, Any]], acols: dict[str, dict[str, Any]], prefix: str
) -> list[Drift]:
    out: list[Drift] = []
    for name in sorted(set(bcols) | set(acols)):
        path = f"{prefix}columns.{name}"
        if name not in bcols:
            out.append(Drift(path, 1, None, acols[name], "column added"))
            continue
        if name not in acols:
            out.append(Drift(path, 1, bcols[name], None, "column removed"))
            continue
        b, a = bcols[name], acols[name]
        if family(b.get("kind")) != family(a.get("kind")):
            out.append(
                Drift(f"{path}.kind", 1, b.get("kind"), a.get("kind"), "type family changed")
            )
        for metric in (*_METRICS, "median"):
            bv, av = _metric(b, metric), _metric(a, metric)
            if bv is None and av is None:
                continue
            s = _rel(bv, av)
            if s:
                out.append(Drift(f"{path}.{metric}", min(1.0, s), bv, av, "relative metric change"))
    return out
