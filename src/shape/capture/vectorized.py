"""High-throughput in-memory columnar profiler.
Exact NumPy fast path for numeric arrays; reference streaming capture remains bounded-memory."""

from __future__ import annotations

from shape.profile.error import ErrorModel


def _numeric(a):
    import numpy as np

    arr = np.asarray(a)
    if arr.dtype.kind not in "iuf":
        raise TypeError("not numeric")
    n = int(arr.size)
    if arr.dtype.kind == "f":
        nan = int(np.isnan(arr).sum())
        pos = int(np.isposinf(arr).sum())
        neg = int(np.isneginf(arr).sum())
        finite = arr[np.isfinite(arr)]
    else:
        nan = pos = neg = 0
        finite = arr
    nulls = 0
    fc = int(finite.size)
    if fc:
        qs = np.quantile(finite, [0.25, 0.5, 0.75])
        mn = finite.min().item()
        mx = finite.max().item()
        mean = float(finite.mean())
        var = float(finite.var())
        distinct = int(np.unique(finite).size)
    else:
        qs = [None] * 3
        mn = mx = mean = var = None
        distinct = 0
    return {
        "kind": "numeric",
        "count": n,
        "null_count": nulls,
        "nan_count": nan,
        "pos_inf_count": pos,
        "neg_inf_count": neg,
        "finite_count": fc,
        "min": mn,
        "max": mx,
        "mean": mean,
        "variance_population": var,
        "q25": None if qs[0] is None else float(qs[0]),
        "q50": None if qs[1] is None else float(qs[1]),
        "q75": None if qs[2] is None else float(qs[2]),
        "distinct_estimate": distinct,
        "topk": [],
        "error_models": {
            "cardinality": ErrorModel("numpy-unique", True).to_dict(),
            "quantiles": ErrorModel("numpy-quantile", True).to_dict(),
        },
    }


def capture_columns(columns: dict):
    """
    Profile equal-length column arrays. This API is optimized for ETL engines that already hold
    columnar batches.
    """
    if not columns:
        return {"rows": 0, "columns": {}}
    lengths = {len(v) for v in columns.values()}
    if len(lengths) != 1:
        raise ValueError("columns must have equal length")
    n = next(iter(lengths))
    out = {}
    for name, v in columns.items():
        try:
            out[name] = _numeric(v)
        except (TypeError, ValueError):
            # correctness fallback; callers seeking maximum throughput should supply
            # encoded/category arrays.
            from shape.capture import capture_rows

            out[name] = capture_rows({name: x} for x in v).to_dict()["columns"][name]
    return {"rows": n, "columns": out}
