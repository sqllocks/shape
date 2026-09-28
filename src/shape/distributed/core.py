"""Deterministic distributed profiling primitives."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

from shape.capture import capture_rows


def partition_rows(rows, partitions):
    rows = list(rows)
    return [rows[i::partitions] for i in range(partitions)]


def _capture(rows):
    rows = list(rows)
    if not rows:
        return {"rows": 0, "columns": {}}
    # Fast columnar path for stable schemas; fall back to streaming capture for sparse/irregular
    # rows.
    keys = tuple(rows[0])
    if all(tuple(r) == keys for r in rows):
        from shape.capture.vectorized import capture_columns

        return capture_columns({k: [r[k] for r in rows] for k in keys})
    return capture_rows(rows).to_dict()


def merge_shapes(shapes):
    if not shapes:
        return {"rows": 0, "columns": {}}
    # Exact merge from sufficient evidence where available; bounded fallback for cardinality.
    out = {"rows": sum(int(s.get("rows", 0)) for s in shapes), "columns": {}}
    names = set().union(*(s.get("columns", {}) for s in shapes))
    for n in names:
        cs = [s["columns"][n] for s in shapes if n in s.get("columns", {})]
        kind = cs[0].get("kind")
        count = sum(int(c.get("count", 0)) for c in cs)
        nulls = sum(int(c.get("null_count", 0)) for c in cs)
        x = {"kind": kind, "count": count, "null_count": nulls}
        if kind == "numeric":
            ns = [max(0, int(c.get("count", 0)) - int(c.get("null_count", 0))) for c in cs]
            N = sum(ns)
            mean = (
                sum(k * float(c.get("mean") or 0) for k, c in zip(ns, cs, strict=False)) / N
                if N
                else None
            )
            if N:
                var = (
                    sum(
                        k
                        * (
                            float(c.get("variance_population") or 0)
                            + (float(c.get("mean") or 0) - mean) ** 2
                        )
                        for k, c in zip(ns, cs, strict=False)
                    )
                    / N
                )
                x.update(
                    mean=mean,
                    variance_population=var,
                    min=min(c["min"] for c in cs if c.get("min") is not None),
                    max=max(c["max"] for c in cs if c.get("max") is not None),
                )
            x["distinct_estimate"] = min(
                count, sum(float(c.get("distinct_estimate") or 0) for c in cs)
            )
        else:
            x["distinct_estimate"] = min(
                count, sum(float(c.get("distinct_estimate") or 0) for c in cs)
            )
        out["columns"][n] = x
    return out


class DistributedProfiler:
    def __init__(self, workers=4, executor="thread"):
        self.workers = workers
        self.executor = executor

    def profile(self, partitions):
        parts = list(partitions)
        E = ThreadPoolExecutor if self.executor == "thread" else ProcessPoolExecutor
        with E(max_workers=self.workers) as ex:
            shapes = list(ex.map(_capture, parts))
        return merge_shapes(shapes)
