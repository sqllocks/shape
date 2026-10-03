"""Seasonality of a numeric column against a table's date or timestamp column (W7-03).
``docs/PROFILING_NOTES.md`` gives the formulas, the minimum sample and when it is not computed.

The values are aggregated (mean) per day, or per hour when the time column spans less than 14
days, in one chunked pass over the table (every numeric column is read once, a chunk at a time).
The candidate periods are 24 on the hourly series, 7 on the daily series, 12 on the monthly means
of daily data and 52 on the weekly means, each when at least three full periods exist. For each
the autocorrelation at the period and the strength of a classical moving-average decomposition
are computed; the strongest period is reported. Pure numpy, so both kernels give the same result.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

CHUNK = 1 << 18
"""Rows per step of the aggregation pass."""
HOURLY_BELOW_DAYS = 14
"""A time column spanning fewer days than this is aggregated per hour, else per day."""
MIN_PERIODS = 3
"""Full periods a candidate period needs."""
SEASONAL_STRENGTH = 0.6
"""``seasonal`` is true at this strength or above."""
MAX_BINS = 2_000_000
"""Most days a time column may span."""
MIN_FILLED = 0.5
"""Share of the periods that must hold a value; the empty ones are filled by interpolation."""
PERIODS = (24, 7, 12, 52)
_DAY = 86400
_HOUR = 3600
_EPOCH = np.datetime64("1970-01-01", "D")
TIME_KINDS = ("dt64", "objdate")
NUMERIC_KINDS = ("int", "float")


def _seconds(chunk: Any) -> np.ndarray:
    """A chunk of a date or timestamp column as int64 seconds since the epoch, nulls as the
    minimum int64 (so they can be dropped)."""
    arr = pa.chunked_array([chunk]) if not isinstance(chunk, pa.ChunkedArray) else chunk
    t = arr.type
    valid = np.asarray(arr.is_valid().to_numpy(zero_copy_only=False), dtype=bool)
    if pa.types.is_date32(t):
        raw = pc.cast(arr, pa.int32()).fill_null(0).to_numpy().astype(np.int64) * _DAY
    elif pa.types.is_date64(t):
        raw = pc.cast(arr, pa.int64()).fill_null(0).to_numpy() // 1000
    elif pa.types.is_timestamp(t):
        per = {"s": 1, "ms": 1000, "us": 1_000_000, "ns": 1_000_000_000}[t.unit]
        raw = pc.cast(arr, pa.int64()).fill_null(0).to_numpy() // per
    else:
        raw = pc.cast(pc.cast(arr, pa.timestamp("s")), pa.int64()).fill_null(0).to_numpy()
    out = np.array(raw, dtype=np.int64)
    out[~valid] = np.iinfo(np.int64).min
    return out


def _floats(chunk: Any) -> np.ndarray:
    if isinstance(chunk, np.ndarray):
        return np.asarray(chunk, dtype=np.float64)
    arr = chunk.combine_chunks() if isinstance(chunk, pa.ChunkedArray) else chunk
    return np.asarray(arr.to_numpy(zero_copy_only=False), dtype=np.float64)


def _slice(arr: Any, a: int, b: int) -> Any:
    return arr[a:b] if isinstance(arr, np.ndarray) else arr.slice(a, b - a)


def time_range(col: Any) -> tuple[int, int, int] | None:
    """The first and last time (seconds) and the count of non-null times of a time column, read
    a chunk at a time."""
    n = len(col.arr)
    floor = np.iinfo(np.int64).min
    lo, hi, count = None, None, 0
    for a in range(0, n, CHUNK):
        sec = _seconds(_slice(col.arr, a, min(a + CHUNK, n)))
        s = sec[sec != floor]
        if len(s):
            count += len(s)
            lo = int(s.min()) if lo is None else min(lo, int(s.min()))
            hi = int(s.max()) if hi is None else max(hi, int(s.max()))
    if lo is None or hi is None:
        return None
    return lo, hi, count


def aggregate(
    time_col: Any, value_cols: Sequence[Any], t0: int, unit: int, bins: int
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Per value column, the sum and count of its finite values in each of ``bins`` periods of
    ``unit`` seconds from ``t0``: one pass, ``CHUNK`` rows at a time."""
    sums = [np.zeros(bins) for _ in value_cols]
    counts = [np.zeros(bins) for _ in value_cols]
    n = len(time_col.arr)
    floor = np.iinfo(np.int64).min
    for a in range(0, n, CHUNK):
        b = min(a + CHUNK, n)
        sec = _seconds(_slice(time_col.arr, a, b))
        ok = sec != floor
        idx = (sec - t0) // unit
        ok &= (idx >= 0) & (idx < bins)
        for j, vc in enumerate(value_cols):
            v = _floats(_slice(vc.arr, a, b))
            sel = ok & np.isfinite(v)
            if not sel.any():
                continue
            i = idx[sel]
            sums[j] += np.bincount(i, weights=v[sel], minlength=bins)
            counts[j] += np.bincount(i, minlength=bins)
    return list(zip(sums, counts, strict=True))


# --- the statistics of one series -----------------------------------------------------------------


def _fill(sums: np.ndarray, counts: np.ndarray) -> np.ndarray | None:
    """The mean of each period, empty ones interpolated; ``None`` when too few hold a value."""
    have = counts > 0
    if not have.any() or have.mean() < MIN_FILLED:
        return None
    y = np.zeros(len(sums))
    y[have] = sums[have] / counts[have]
    if not have.all():
        x = np.arange(len(sums))
        y[~have] = np.interp(x[~have], x[have], y[have])
    return y


def acf_at(y: np.ndarray, lag: int) -> float:
    """The sample autocorrelation of ``y`` at ``lag`` (0 for a constant series)."""
    d = y - y.mean()
    denom = float((d * d).sum())
    if not denom > 0.0 or lag >= len(y):
        return 0.0
    return float((d[:-lag] * d[lag:]).sum() / denom)


def _trend(y: np.ndarray, period: int) -> np.ndarray:
    """The centred moving average of window ``period`` (2 x period for an even one); NaN where
    the window does not fit."""
    n = len(y)
    out = np.full(n, np.nan)
    c = np.concatenate([[0.0], np.cumsum(y)])
    if period % 2 == 1:
        h = period // 2
        out[h : n - h] = (c[period:] - c[:-period]) / period
    else:
        h = period // 2
        w = (c[period:] - c[:-period]) / period  # means of length-period windows
        out[h : n - h] = 0.5 * (w[:-1] + w[1:])
    return out


def strength(y: np.ndarray, period: int) -> float:
    """``max(0, 1 - var(remainder) / var(seasonal + remainder))`` from the classical
    decomposition ``y = trend + seasonal + remainder``: the trend is the centred moving average,
    the seasonal figure the mean of the detrended values at each position of the period (centred
    to sum to zero)."""
    trend = _trend(y, period)
    ok = ~np.isnan(trend)
    detrended = (y - trend)[ok]
    pos = (np.arange(len(y)) % period)[ok]
    sums = np.bincount(pos, weights=detrended, minlength=period)
    cnt = np.bincount(pos, minlength=period)
    figure = np.where(cnt > 0, sums / np.maximum(cnt, 1), 0.0)
    figure -= figure.mean()
    remainder = detrended - figure[pos]
    total = float(detrended.var())
    if not total > 0.0:
        return 0.0
    return max(0.0, 1.0 - float(remainder.var()) / total)


def _calendar(day0: int, days: int, kind: str) -> np.ndarray:
    """The month number (``kind == "month"``) or week number of each of ``days`` days from epoch
    day ``day0``, counted from the first one."""
    d = _EPOCH + (day0 + np.arange(days)).astype("timedelta64[D]")
    if kind == "month":
        m = d.astype("datetime64[M]").astype(np.int64)
        return np.asarray(m - m[0])
    return np.arange(days) // 7


def _regroup(sums: np.ndarray, counts: np.ndarray, group: np.ndarray) -> tuple[np.ndarray, ...]:
    k = int(group[-1]) + 1
    return np.bincount(group, weights=sums, minlength=k), np.bincount(
        group, weights=counts, minlength=k
    )


def _candidates(
    sums: np.ndarray, counts: np.ndarray, hourly: bool, day0: int
) -> list[tuple[str, int, np.ndarray, np.ndarray]]:
    """``(granularity, period, sums, counts)`` of each candidate period with at least
    :data:`MIN_PERIODS` full periods."""
    if hourly:
        series = [("hour", 24, sums, counts)]
    else:
        series = [("day", 7, sums, counts)]
        days = len(sums)
        for kind, gran, period in (("month", "month", 12), ("week", "week", 52)):
            s, c = _regroup(sums, counts, _calendar(day0, days, kind))
            series.append((gran, period, s, c))
    return [s for s in series if len(s[2]) >= MIN_PERIODS * s[1]]


def _na(reason: str, time_column: str | None) -> dict[str, Any]:
    return {"applicable": False, "time_column": time_column, "reason": reason}


def analyse(
    time_column: str, sums: np.ndarray, counts: np.ndarray, hourly: bool, day0: int
) -> dict[str, Any]:
    """The ``seasonality`` entry of one column from its per-period sums and counts."""
    cands = _candidates(sums, counts, hourly, day0)
    if not cands:
        return _na(
            "fewer than three full periods of any candidate period "
            "(24 hours, 7 days, 12 months, 52 weeks)",
            time_column,
        )
    best: tuple[float, int, str, float] | None = None
    sparse = constant = 0
    for gran, period, s, c in cands:
        y = _fill(s, c)
        if y is None:
            sparse += 1
            continue
        if not float(y.var()) > 0.0:
            constant += 1
            continue
        st = strength(y, period)
        if best is None or st > best[0] + 1e-12:
            best = (st, period, gran, acf_at(y, period))
    if best is None:
        if constant:
            return _na("constant series", time_column)
        return _na("more than half of the periods hold no value", time_column)
    st, period, gran, acf = best
    return {
        "applicable": True,
        "time_column": time_column,
        "granularity": gran,
        "period": period,
        "strength": round(st, 6),
        "acf": round(acf, 6),
        "seasonal": bool(st >= SEASONAL_STRENGTH),
    }


def find_time_column(cols: Sequence[Any], dtypes: dict[str, str], named: str | None) -> Any:
    """The time column of a table: the one named, else the only date or timestamp column. Returns
    the column, or a string giving the reason there is none."""
    stamps = [
        c for c in cols if c.kind in TIME_KINDS and dtypes.get(c.name) in ("date", "datetime")
    ]
    if named is not None:
        for c in cols:
            if c.name == named:
                if c in stamps:
                    return c
                raise ValueError(f"time_column {named!r} is not a date or timestamp column")
        return "no column named " + repr(named)
    if not stamps:
        return "no date or timestamp column"
    if len(stamps) > 1:
        return "more than one date or timestamp column (name one with time_column)"
    return stamps[0]


def table_seasonality(
    cols: Sequence[Any], dtypes: dict[str, str], time_column: str | None
) -> dict[str, dict[str, Any]]:
    """The ``seasonality`` entry of every numeric column of a table (by name). ``cols`` are the
    table's columns, ``dtypes`` the profile's dtype of each, ``time_column`` the named one (or
    ``None``: the only date or timestamp column)."""
    numeric = [
        c for c in cols if c.kind in NUMERIC_KINDS and dtypes.get(c.name) in ("integer", "float")
    ]
    if not numeric:
        return {}
    tc = find_time_column(cols, dtypes, time_column)
    if isinstance(tc, str):
        return {c.name: _na(tc, time_column) for c in numeric}
    numeric = [c for c in numeric if c.name != tc.name]
    rng = time_range(tc)
    if rng is None:
        return {c.name: _na("no non-null times", tc.name) for c in numeric}
    lo, hi, _ = rng
    if hi - lo > MAX_BINS * _DAY:
        return {c.name: _na("the time column spans too many days", tc.name) for c in numeric}
    hourly = (hi - lo) < HOURLY_BELOW_DAYS * _DAY
    unit = _HOUR if hourly else _DAY
    t0 = (lo // unit) * unit
    bins = (hi - t0) // unit + 1
    aggregated = aggregate(tc, numeric, t0, unit, int(bins))
    day0 = t0 // _DAY
    return {
        c.name: analyse(tc.name, s, n, hourly, day0)
        for c, (s, n) in zip(numeric, aggregated, strict=True)
    }


def describe(col: dict[str, Any]) -> list[str]:
    """Text lines for the ``seasonality`` of one column profile."""
    s = col.get("seasonality")
    if not isinstance(s, dict):
        return []
    if not s.get("applicable"):
        return [f"seasonality: not computed ({s.get('reason')})"]
    unit = {"hour": "hours", "day": "days", "week": "weeks", "month": "months"}[s["granularity"]]
    flag = "seasonal" if s.get("seasonal") else "not seasonal"
    return [f"seasonality: period {s['period']} {unit}, strength {s['strength']:.2f} ({flag})"]
