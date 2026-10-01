"""Pure-Python twin of the exact-mode column kernels (``rust/shape-kernel/src/exact.rs``, P1-15).

These are the numpy/pyarrow computations the product profiler used before they moved into the
native kernel; they stay as the correctness oracle (``SHAPE_KERNEL=python``) and the fallback of
the pure wheel. Same arguments and results as the native functions.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

PCTS = [1, 5, 10, 25, 50, 75, 90, 95, 99]
_ALL_PCTS = [*PCTS, 0.5, 99.5]


def linear_index(n: int, qs: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """numpy 'linear' method: virtual index (n-1)*q, _get_indexes bounds handling."""
    q = np.true_divide(np.asarray(qs, dtype=np.float64), 100)
    vi = (n - 1) * q
    prev = np.floor(vi)
    nxt = prev + 1
    above = vi >= n - 1
    prev[above] = -1
    nxt[above] = -1
    below = vi < 0
    prev[below] = 0
    nxt[below] = 0
    prev = prev.astype(np.intp)
    nxt = nxt.astype(np.intp)
    gamma = np.asarray(vi - prev, dtype=vi.dtype)
    return prev, nxt, gamma


def lerp(a: np.ndarray, b: np.ndarray, t: np.ndarray) -> np.ndarray:
    """numpy's ``_lerp``."""
    diff = b - a
    res: np.ndarray = np.add(a, diff * t)
    np.subtract(b, diff * (1 - t), out=res, where=t >= 0.5, casting="unsafe", dtype=res.dtype)
    return res


def percentile_sorted(sorted_a: np.ndarray, qs: Any) -> np.ndarray:
    """``np.percentile(data, qs)`` (linear) on already-sorted data: identical virtual indices,
    neighbours and ``_lerp``, hence bitwise-identical results."""
    prev, nxt, gamma = linear_index(sorted_a.shape[0], qs)
    return lerp(sorted_a[prev], sorted_a[nxt], gamma)


def top_by_first_seen(
    values: np.ndarray, uniq: np.ndarray, counts: np.ndarray, need: int
) -> np.ndarray:
    """Indices into `uniq` of the first `need` keys in pandas' value_counts order
    (count desc, ties by first appearance in row order), without hashing every row:
    keys above the need-th count are always selected; ties at that count are resolved by
    scanning rows in order (chunked, vectorised) until enough first appearances are seen."""
    k = len(uniq)
    need = min(need, k)
    c_thr = np.partition(counts, k - need)[k - need]
    n_above = int((counts > c_thr).sum())
    want_ties = need - n_above
    first = np.full(k, -1, dtype=np.int64)
    found_above = found_ties = 0
    pos, chunk, n = 0, 1 << 14, len(values)
    # Only keys with count >= c_thr can be selected: search that (usually tiny, cache-resident)
    # subset instead of every distinct key.
    cand_idx = np.flatnonzero(counts >= c_thr)
    cand = uniq[cand_idx]
    last = len(cand) - 1
    while pos < n and (found_above < n_above or found_ties < want_ties):
        ch = values[pos : pos + chunk]
        cpos = np.minimum(np.searchsorted(cand, ch), last)
        rows = np.flatnonzero(cand[cpos] == ch)
        idx = cand_idx[cpos]
        if len(rows):
            u, fi = np.unique(idx[rows], return_index=True)
            new = first[u] < 0
            u, fi = u[new], fi[new]
            first[u] = pos + rows[fi]
            ca = counts[u]
            found_above += int((ca > c_thr).sum())
            found_ties += int((ca == c_thr).sum())
        pos += chunk
        chunk *= 2
    above = np.flatnonzero(counts > c_thr)
    ties = np.flatnonzero((counts == c_thr) & (first >= 0))
    ties = ties[np.argsort(first[ties], kind="stable")][:want_ties]
    sel = np.concatenate([above, ties])
    order = np.lexsort((first[sel], -counts[sel]))
    return sel[order]


def _need_for(card: int, top_n: int, row_count: int) -> int:
    ratio = card / row_count if row_count > 0 else 0.0
    is_enum = (card < 200 or (ratio < 0.30 and card < 50_000)) and card > 0
    return card if is_enum else min(top_n, card)


def count_numeric(
    values: Any,
    top_n: int,
    row_count: int,
    want_sorted: bool = False,
    want_uniq: bool = False,
) -> dict[str, Any]:
    """Distinct count and the leading keys of a float64/int64 array without nulls in pandas'
    ``value_counts`` order (every key for an enum column, else the first ``top_n``)."""
    row = np.asarray(values.to_numpy(zero_copy_only=False))
    xs = np.sort(row)
    starts = np.flatnonzero(np.concatenate(([True], xs[1:] != xs[:-1])))
    uniq = xs[starts]
    counts = np.diff(np.append(starts, len(xs)))
    top = top_by_first_seen(row, uniq, counts, _need_for(len(uniq), top_n, row_count))
    return {
        "cardinality": len(uniq),
        "keys": pa.array(uniq[top]),
        "counts": pa.array(counts[top].astype(np.int64)),
        "sorted": pa.array(xs.astype(np.float64, copy=False)) if want_sorted else None,
        "uniq": pa.array(uniq) if want_uniq else None,
        "all_whole": bool(np.all(uniq == uniq.astype(np.int64))),
    }


def numeric_stats(values: Any, sorted: Any = None) -> dict[str, Any]:  # noqa: A002
    """Mean, sample standard deviation, the 11 quantiles (p1..p99, p0.5, p99.5) and the count of
    1.5 x IQR outliers (``None`` when the IQR is zero) of a float64 array without nulls."""
    numeric = values.to_numpy(zero_copy_only=False)
    cnt = len(numeric)
    s = numeric.sum(dtype=np.float64)
    mean = float(s / cnt)
    if cnt > 1:
        avg = s / cnt
        std = float(np.sqrt(((numeric - avg) ** 2).sum() / (cnt - 1)))
    else:
        std = float("nan")
    if cnt < 4:
        return {
            "mean": mean,
            "std": std,
            "quantiles": None,
            "has_quantiles": False,
            "outliers": None,
        }
    xs = sorted.to_numpy(zero_copy_only=False) if sorted is not None else np.sort(numeric)
    vals = percentile_sorted(xs, _ALL_PCTS)
    q1, q3 = vals[3], vals[5]
    iqr = q3 - q1
    outliers = None
    if iqr != 0:
        lo_f = q1 - 1.5 * iqr
        hi_f = q3 + 1.5 * iqr
        outliers = int(
            np.searchsorted(xs, lo_f, "left") + (cnt - np.searchsorted(xs, hi_f, "right"))
        )
    return {
        "mean": mean,
        "std": std,
        "quantiles": [float(v) for v in vals],
        "has_quantiles": True,
        "outliers": outliers,
    }


def value_counts_str(values: Any) -> tuple[Any, Any]:
    """``pc.value_counts`` of a string array without nulls: distinct values in first-appearance
    order and their counts (int64)."""
    vc = pc.value_counts(values)
    return vc.field("values"), vc.field("counts").cast(pa.int64())


def top_indices(counts: Any, need: int) -> Any:
    """``np.argsort(-counts, kind="stable")[:need]`` as an int64 array."""
    c = counts.to_numpy(zero_copy_only=False)
    return pa.array(np.argsort(-c, kind="stable")[:need].astype(np.int64))


def temporal_counts(ts: Any) -> dict[str, Any]:
    """Hour-of-day, day-of-week (from Monday), month and year counts of a timestamp array
    without nulls; ``years[i]`` counts year ``year0 + i``."""
    hc = np.bincount(pc.hour(ts).to_numpy(), minlength=24)
    dc = np.bincount(pc.day_of_week(ts).to_numpy(), minlength=7)
    years = pc.year(ts).to_numpy().astype(int)
    months = pc.month(ts).to_numpy().astype(int)
    out: dict[str, Any] = {
        "hour": hc.tolist(),
        "dow": dc.tolist(),
        "month": np.bincount(months - 1, minlength=12).tolist(),
    }
    if len(years):
        y0 = int(years.min())
        out["year0"] = y0
        out["years"] = np.bincount(years - y0).tolist()
    return out


def float_repr(values: Any) -> Any:
    """``[str(float(v)) for v in values]`` (Python's shortest repr) of a float64 array without
    nulls, as a string array."""
    return pa.array(
        [str(float(v)) for v in values.to_numpy(zero_copy_only=False).tolist()], pa.string()
    )


def round6(values: Any) -> Any:
    """``[round(float(v), 6) for v in values]`` of a float64 array without nulls."""
    return pa.array(
        [round(float(v), 6) for v in values.to_numpy(zero_copy_only=False).tolist()], pa.float64()
    )


def date_iso(values: Any) -> Any:
    """``date32`` as ``YYYY-MM-DD`` text (pyarrow's cast); nulls stay null."""
    return pc.cast(values, pa.string())
