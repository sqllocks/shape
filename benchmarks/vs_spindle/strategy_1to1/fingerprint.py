"""Compact, JSON-able fingerprints of one generated column (numpy + the standard library only,
so the Spindle venv and the Shape venv compute them with the same code).

A fingerprint keeps what the T-21 clauses (b)-(e) need: the null rate, a 2001-point quantile grid
(KS to within 0.0005), exact value counts for low-cardinality columns, and for strings the length
histogram, the character class at each position and the character-class masks.
"""

from __future__ import annotations

import datetime as dt
import math
from collections import Counter
from collections.abc import Sequence
from typing import Any

import numpy as np

GRID = 2001
MAX_COUNTED = 1000  # distinct values kept as exact counts
MAX_POSITIONS = 48
MAX_MASKS = 64


def _is_null(v: Any) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _char_class(c: str) -> str:
    if c.isdigit():
        return "9"
    if c.isalpha():
        return "A" if c.isupper() else "a"
    return c


def fingerprint(values: Sequence[Any]) -> dict[str, Any]:
    n = len(values)
    present = [v for v in values if not _is_null(v)]
    fp: dict[str, Any] = {"n": n, "null_count": n - len(present)}
    if not present:
        fp["kind"] = "empty"
        return fp
    first = present[0]
    if isinstance(first, str):
        fp.update(_string(present))
    elif isinstance(first, dt.datetime | np.datetime64) or hasattr(first, "to_pydatetime"):
        ns = np.array([np.datetime64(v, "ns").astype(np.int64) for v in present], dtype=np.float64)
        fp.update(_numeric(ns, False))
        fp["kind"] = "datetime"
        fp["calendar"] = _calendar(ns)
    else:
        arr = np.asarray(present, dtype=np.float64)
        integral = all(isinstance(v, int | np.integer) and not isinstance(v, bool) for v in present)
        fp.update(_numeric(arr, integral))
        if isinstance(first, bool | np.bool_):
            fp["dtype"] = "bool"
    return fp


def _calendar(ns: np.ndarray[Any, Any]) -> dict[str, Any]:
    """Counts of timestamps by month, weekday (Monday = 0) and hour of day, and the share that
    falls on a whole second (a profile with an hour of day gives whole seconds)."""
    t = ns.astype(np.int64).astype("datetime64[ns]")
    days = t.astype("datetime64[D]")
    month = days.astype("datetime64[M]").astype(np.int64) % 12
    dow = (days.astype(np.int64) + 3) % 7
    hour = (t - days).astype("timedelta64[h]").astype(np.int64)
    whole = ((t - t.astype("datetime64[s]")).astype(np.int64) == 0).mean()
    return {
        "month": np.bincount(month, minlength=12).tolist(),
        "dow": np.bincount(dow, minlength=7).tolist(),
        "hour": np.bincount(hour, minlength=24).tolist(),
        "whole_second_rate": float(whole),
    }


def _numeric(arr: np.ndarray[Any, Any], integral: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "kind": "numeric",
        "dtype": "int" if integral else "float",
        "quantiles": np.quantile(arr, np.linspace(0.0, 1.0, GRID)).tolist(),
        "mean": float(arr.mean()),
        "std": float(arr.std()),
    }
    uniq, counts = np.unique(arr, return_counts=True)
    out["n_distinct"] = int(len(uniq))
    out["is_unique"] = bool(len(uniq) == len(arr))
    out["is_increasing"] = bool(len(arr) < 2 or (np.diff(arr) > 0).all())
    if len(uniq) <= 200:
        out["counts"] = {repr(float(u)): int(c) for u, c in zip(uniq, counts, strict=True)}
    return out


def _string(present: list[str]) -> dict[str, Any]:
    counts = Counter(present)
    out: dict[str, Any] = {"kind": "string", "dtype": "string", "n_distinct": len(counts)}
    out["is_unique"] = len(counts) == len(present)
    if len(counts) <= MAX_COUNTED:
        out["counts"] = dict(counts)
    out["lengths"] = {str(k): v for k, v in sorted(Counter(len(s) for s in present).items())}
    positions: list[list[int]] = [[0, 0, 0, 0] for _ in range(MAX_POSITIONS)]
    index = {"A": 0, "a": 1, "9": 2}
    masks: Counter[str] = Counter()
    for s in present:
        for i, ch in enumerate(s[:MAX_POSITIONS]):
            positions[i][index.get(_char_class(ch), 3)] += 1
        masks["".join(_char_class(c) for c in s)] += 1
    out["position_classes"] = positions
    out["masks"] = dict(masks.most_common(MAX_MASKS))
    out["n_masks"] = len(masks)
    return out
