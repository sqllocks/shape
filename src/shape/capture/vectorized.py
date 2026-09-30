"""High-throughput in-memory columnar capture, on the fused profile kernel.

``capture_columns`` profiles equal-length column arrays with one kernel call per record batch.
It emits the same per-column schema as the row path (``capture_rows``): the same keys, the
same nesting and the same ``error_models`` entries, so a shape captured either way can be
compared without spurious drift (P12). Numeric and text columns go through the kernel (P13:
text is no longer profiled cell by cell in Python); nulls are real nulls, not NaN (P10).
Columns the kernel does not summarise (booleans, dates and timestamps, nested values) are
captured by the row path, which is the reference for their schema.
"""

from __future__ import annotations

import math
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.kernel.dispatch import get_kernel
from shape.profile.error import ErrorModel, hll_error, kll_error

_TOP = 10
_EXACT_CARD = ErrorModel("exact-hash-table", True)
_EXACT_QUANTILE = ErrorModel("exact-sort", True)


def _topk(top: list[Any]) -> list[list[Any]]:
    return [[value, count, error] for value, count, error, _first in top[:_TOP]]


def _distinct(stats: dict[str, Any]) -> float | int:
    d = stats["distinct"]
    return int(d) if stats["distinct_exact"] else float(d)


def _cardinality(stats: dict[str, Any]) -> dict[str, Any]:
    model = _EXACT_CARD if stats["distinct_exact"] else hll_error(14)
    return model.to_dict()


def _numeric(stats: dict[str, Any]) -> dict[str, Any]:
    finite = stats["finite_count"]
    q = stats["quantiles"]
    exact = stats["distinct_exact"]
    return {
        "kind": "numeric",
        "count": stats["count"],
        "null_count": stats["null_count"],
        "nan_count": stats["nan_count"],
        "pos_inf_count": stats["pos_inf_count"],
        "neg_inf_count": stats["neg_inf_count"],
        "finite_count": finite,
        "min": stats["min"],
        "max": stats["max"],
        "mean": stats["mean"] if finite else None,
        "variance_population": stats["m2"] / finite if finite else None,
        "q25": q.get(0.25),
        "q50": q.get(0.5),
        "q75": q.get(0.75),
        "distinct_estimate": _distinct(stats),
        "topk": _topk(stats["top"]),
        "error_models": {
            "cardinality": _cardinality(stats),
            "quantiles": (_EXACT_QUANTILE if exact else kll_error(200)).to_dict(),
        },
    }


def _hist_quantile(hist: dict[int, int], total: int, q: float) -> float:
    """numpy's linear percentile over a histogram of integer values."""
    pos = q * (total - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, total - 1)
    t = pos - lo

    def at(rank: int) -> float:
        acc = 0
        for value in sorted(hist):
            acc += hist[value]
            if rank < acc:
                return float(value)
        return 0.0

    a, b = at(lo), at(hi)
    return b - (b - a) * (1 - t) if t >= 0.5 else a + (b - a) * t


def _length_summary(length: dict[str, Any], exact: bool) -> dict[str, Any]:
    n = length["count"]
    hist: dict[int, int] = length.get("hist", {})
    out: dict[str, Any] = {
        "count": n,
        "null_count": 0,
        "nan_count": 0,
        "pos_inf_count": 0,
        "neg_inf_count": 0,
        "finite_count": n,
        "min": None,
        "max": None,
        "mean": None,
        "variance_population": None,
        "q25": None,
        "q50": None,
        "q75": None,
        "distinct_estimate": float(len(hist)),
        "topk": [],
    }
    if n:
        mean = length["mean"]
        m2 = sum(c * (v - mean) ** 2 for v, c in hist.items())
        top = sorted(hist.items(), key=lambda kv: (-kv[1], kv[0]))[:_TOP]
        out.update(
            min=length["min"],
            max=length["max"],
            mean=mean,
            variance_population=m2 / n,
            q25=_hist_quantile(hist, n, 0.25),
            q50=_hist_quantile(hist, n, 0.5),
            q75=_hist_quantile(hist, n, 0.75),
            topk=[[v, c, 0] for v, c in top],
        )
    return out


def _text(stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "text",
        "count": stats["count"],
        "null_count": stats["null_count"],
        "length": _length_summary(stats["length"], stats["distinct_exact"]),
        "distinct_estimate": _distinct(stats),
        "topk": _topk(stats["top"]),
        "error_models": {"cardinality": _cardinality(stats)},
    }


def _as_arrow(values: Any) -> Any:
    """An Arrow array for one input column, or None when it has no single Arrow type (for
    example a list mixing numbers and text): the row path then handles it."""
    if isinstance(values, (pa.Array, pa.ChunkedArray)):
        return values
    try:
        return pa.array(values)
    except (pa.ArrowInvalid, pa.ArrowTypeError, TypeError, ValueError):
        return None


def capture_columns(columns: dict[str, Any], mode: str = "exact") -> dict[str, Any]:
    """Profile equal-length column arrays. ``mode`` is ``"exact"`` (default) or ``"bounded"``
    (sketches for distinct counts, top values and quantiles)."""
    if not columns:
        return {"rows": 0, "columns": {}}
    if len({len(v) for v in columns.values()}) != 1:
        raise ValueError("columns must have equal length")
    n = len(next(iter(columns.values())))
    arrays = {name: _as_arrow(v) for name, v in columns.items()}
    fast = {
        name: a
        for name, a in arrays.items()
        if a is not None
        and (
            pa.types.is_integer(a.type)
            or pa.types.is_floating(a.type)
            or pa.types.is_decimal128(a.type)
            or pa.types.is_string(a.type)
            or pa.types.is_large_string(a.type)
        )
    }
    out: dict[str, Any] = {}
    if fast:
        table = pa.table(fast)
        state = get_kernel().ProfileState(table.schema, mode)
        for batch in table.to_batches():
            state.update(batch)
        for stats in state.finalize()["columns"]:
            out[stats["name"]] = (
                _numeric(stats) if stats["kind"] in ("int", "float") else _text(stats)
            )
    for name, values in columns.items():
        if name in out:
            continue
        # correctness fallback: the row path is the schema reference for the other kinds
        from shape.capture import capture_rows

        items = arrays[name].to_pylist() if arrays[name] is not None else list(values)
        out[name] = capture_rows({name: x} for x in items).to_dict()["columns"][name]
    return {"rows": n, "columns": {name: out[name] for name in columns}}
