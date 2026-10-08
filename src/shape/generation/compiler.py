"""Compile captured Shape evidence into an executable synthetic-data generator."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class GenerationReport:
    rows: int
    seed: int
    fields: tuple[str, ...]
    preserved: tuple[str, ...]
    degraded: tuple[str, ...]


def _numeric_column(summary, n, rng):
    import numpy as np

    mean = float(summary.get("mean") or 0.0)
    var = max(0.0, float(summary.get("variance_population") or 0.0))
    sd = math.sqrt(var)
    lo = summary.get("min")
    hi = summary.get("max")
    x = rng.normal(mean, sd, n) if sd else np.full(n, mean, dtype=np.float64)
    if lo is not None or hi is not None:
        x = np.clip(x, -np.inf if lo is None else float(lo), np.inf if hi is None else float(hi))
    nulls = int(summary.get("null_count") or 0)
    if nulls and n:
        k = min(n, round(n * nulls / max(1, int(summary.get("count") or n))))
        idx = rng.choice(n, k, replace=False)
        x = x.astype(object)
        x[idx] = None
    return x


def _text_column(summary, n, rng):
    import numpy as np

    top = summary.get("topk") or []
    vals = []
    weights = []
    for item in top:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            vals.append(str(item[0]))
            weights.append(max(0, float(item[1])))
    if vals and sum(weights) > 0:
        p = np.asarray(weights, dtype=float)
        p /= p.sum()
        out = np.asarray(vals, dtype=object)[rng.choice(len(vals), size=n, p=p)]
    else:
        distinct = max(1, int(round(float(summary.get("distinct_estimate") or min(n, 1000)))))
        out = np.asarray([f"value_{i % distinct}" for i in range(n)], dtype=object)
    nulls = int(summary.get("null_count") or 0)
    if nulls and n:
        k = min(n, round(n * nulls / max(1, int(summary.get("count") or n))))
        out[rng.choice(n, k, replace=False)] = None
    return out


def generate_from_shape(
    shape: Mapping[str, Any],
    n: int | None = None,
    seed: int = 0,
    relationships: Mapping[str, Any] | None = None,
):
    """Generate columnar data from portable Shape evidence.

    relationships supports:
      correlations: [{"source":"x","target":"y","rho":0.8}]
      foreign_keys: [{"field":"customer_id","parent_count":1000}]
      conditionals: [{"when":"segment","equals":"A","field":"score","mean":10,"stddev":2}]
    """
    import numpy as np

    if n is None:
        n = int(shape.get("rows") or 0)
    if n < 0:
        raise ValueError("n")
    rng = np.random.default_rng(seed)
    cols = {}
    degraded = []
    preserved = []
    for name, s in shape.get("columns", {}).items():
        if s.get("kind") == "numeric":
            cols[name] = _numeric_column(s, n, rng)
            preserved.append(f"marginal:{name}")
        else:
            cols[name] = _text_column(s, n, rng)
            preserved.append(f"categorical:{name}")
    rel = relationships or shape.get("relationships") or {}
    for fk in rel.get("foreign_keys", ()):
        field = fk["field"]
        pc = int(fk["parent_count"])
        cols[field] = rng.integers(0, pc, size=n, dtype=np.int64)
        preserved.append(f"foreign_key:{field}")
    for c in rel.get("correlations", ()):
        a, b, rho = c["source"], c["target"], float(c["rho"])
        if a not in cols or b not in cols:
            degraded.append(f"correlation:{a}:{b}")
            continue
        try:
            x = np.asarray(cols[a], dtype=float)
            bs = shape["columns"][b]
            mu = float(bs.get("mean") or 0)
            sd = math.sqrt(max(0, float(bs.get("variance_population") or 0)))
            xm = np.nanmean(x)
            xs = np.nanstd(x)
            if xs > 0 and sd > 0:
                z = rng.normal(0, 1, n)
                cols[b] = mu + sd * (rho * ((x - xm) / xs) + math.sqrt(max(0, 1 - rho * rho)) * z)
                preserved.append(f"correlation:{a}:{b}")
        except Exception:
            degraded.append(f"correlation:{a}:{b}")
    for rule in rel.get("conditionals", ()):
        src, field = rule["when"], rule["field"]
        if src not in cols or field not in cols:
            degraded.append(f"conditional:{src}:{field}")
            continue
        mask = np.asarray(cols[src], dtype=object) == rule.get("equals")
        k = int(mask.sum())
        if k:
            vals = rng.normal(float(rule.get("mean", 0)), max(0, float(rule.get("stddev", 1))), k)
            arr = np.asarray(cols[field]).copy()
            arr[mask] = vals
            cols[field] = arr
        preserved.append(f"conditional:{src}:{field}")
    return cols, GenerationReport(n, seed, tuple(cols), tuple(preserved), tuple(degraded))


def generate_relational(
    shapes: Mapping[str, Mapping[str, Any]], rows: Mapping[str, int], relations, seed=0
):
    """Generate multiple tables and enforce declared FK relationships."""
    import numpy as np

    out = {}
    for i, (name, s) in enumerate(shapes.items()):
        out[name] = generate_from_shape(s, rows.get(name), seed + i)[0]
    for i, r in enumerate(relations):
        parent, child = r["parent"], r["child"]
        pk = r["parent_key"]
        fk = r["child_fk"]
        pn = len(next(iter(out[parent].values()))) if out[parent] else rows[parent]
        if pk not in out[parent]:
            out[parent][pk] = np.arange(pn, dtype=np.int64)
        keys = np.asarray(out[parent][pk])
        cn = len(next(iter(out[child].values()))) if out[child] else rows[child]
        rng = np.random.default_rng(seed + 1000 + i)
        out[child][fk] = keys[rng.integers(0, len(keys), size=cn)]
    return out
