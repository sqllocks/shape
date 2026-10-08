"""Temporal evidence: sequence autocorrelation, the month, day-of-week and hour profiles of a
timestamp column, holiday lifts and the tail index of a numeric column.

The profile functions are the inverse of :func:`shape.generation.temporal.sample_timestamps`:
profile what it generated and the profiles come back (``tests/generation/test_calendars.py``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]


def lag_autocorrelation(values: Iterable[Any], lag: int = 1) -> float | None:
    xs = [float(x) for x in values if x is not None]
    if lag < 1 or len(xs) <= lag:
        return None
    m = sum(xs) / len(xs)
    den = sum((x - m) ** 2 for x in xs)
    if den == 0:
        return 1.0
    return sum((xs[i] - m) * (xs[i - lag] - m) for i in range(lag, len(xs))) / den


US_PER_DAY = 86_400_000_000
_EPOCH = date(1970, 1, 1)


class HolidaySource(Protocol):
    """A calendar that can list its holidays (the rule calendars can)."""

    def holidays(self, start: date, end: date) -> dict[date, str]: ...


@dataclass(frozen=True, slots=True)
class TemporalProfile:
    """What a timestamp column looks like: ``month`` (12 shares, January first),
    ``day_of_week`` (7, Monday first) and ``hour`` (24; ``None`` for dates without a time of
    day) each sum to 1; ``holiday_lifts`` is the observed lift of every holiday of the calendar
    that was given (1.0 is no effect)."""

    n: int
    start: date
    end: date
    month: tuple[float, ...]
    day_of_week: tuple[float, ...]
    hour: tuple[float, ...] | None
    holiday_lifts: dict[str, float] = field(default_factory=dict)


def _micros(values: pa.Array | pa.ChunkedArray) -> npt.NDArray[np.int64]:
    """Microseconds since the epoch of every non-null timestamp or date."""
    arr = values.combine_chunks() if isinstance(values, pa.ChunkedArray) else values
    t = arr.type
    if pa.types.is_date(t):
        arr = pc.cast(pc.cast(arr, pa.date32()), pa.timestamp("us"))
    elif pa.types.is_timestamp(t):  # nanoseconds are cut to microseconds (#316)
        arr = pc.cast(arr.cast(pa.timestamp(t.unit)), pa.timestamp("us", tz=None), safe=False)
    else:
        raise ValueError(f"expected a timestamp or date column, got {t}")
    arr = pc.drop_null(arr)
    return np.asarray(arr.cast(pa.int64()).to_numpy(zero_copy_only=False), dtype=np.int64)


def _shares(counts: npt.NDArray[Any]) -> tuple[float, ...]:
    total = float(counts.sum())
    return tuple(float(c) / total for c in counts)


def temporal_profile(
    values: pa.Array | pa.ChunkedArray, calendar: HolidaySource | None = None
) -> TemporalProfile:
    """The :class:`TemporalProfile` of a timestamp or date column (nulls are ignored)."""
    us = _micros(values)
    if len(us) == 0:
        raise ValueError("a temporal profile needs at least one non-null value")
    days = np.floor_divide(us, US_PER_DAY)
    months = days.astype("datetime64[D]").astype("datetime64[M]").astype(np.int64) % 12
    dow = (days + 3) % 7
    in_day = us - days * US_PER_DAY
    has_time = bool(in_day.any())
    hour = _shares(np.bincount(in_day // 3_600_000_000, minlength=24)) if has_time else None
    start, end = _EPOCH + timedelta(days=int(days.min())), _EPOCH + timedelta(days=int(days.max()))
    lifts = holiday_lifts(values, calendar.holidays(start, end)) if calendar is not None else {}
    return TemporalProfile(
        n=len(us),
        start=start,
        end=end,
        month=_shares(np.bincount(months, minlength=12)),
        day_of_week=_shares(np.bincount(dow, minlength=7)),
        hour=hour,
        holiday_lifts=lifts,
    )


def holiday_lifts(
    values: pa.Array | pa.ChunkedArray,
    holidays: Mapping[date, str],
    exclude_days: int = 3,
) -> dict[str, float]:
    """The lift of each holiday: the rows on its dates divided by the rows expected on an
    ordinary day of the same month and weekday.

    The ordinary level of a (month, weekday) pair is the mean daily count over the days of that
    pair that are further than ``exclude_days`` from any holiday. A holiday's lift pools its
    occurrences (the sum of the counts over the sum of the expectations), so several years
    average out the noise. Holidays outside the data's date range, or whose pair has no ordinary
    day, are left out.
    """
    days = np.floor_divide(_micros(values), US_PER_DAY)
    if len(days) == 0:
        raise ValueError("holiday lifts need at least one non-null value")
    lo, hi = int(days.min()), int(days.max())
    counts = np.bincount(days - lo, minlength=hi - lo + 1).astype(np.float64)
    axis = np.arange(lo, hi + 1)
    month = axis.astype("datetime64[D]").astype("datetime64[M]").astype(np.int64) % 12
    dow = (axis + 3) % 7
    event_days = sorted((d - _EPOCH).days for d in holidays if lo <= (d - _EPOCH).days <= hi)
    ordinary = np.ones(len(axis), dtype=bool)
    for d in event_days:
        ordinary[max(d - exclude_days - lo, 0) : d + exclude_days + 1 - lo] = False
    level = np.full((12, 7), np.nan)
    for m in range(12):
        for w in range(7):
            sel = ordinary & (month == m) & (dow == w)
            if sel.any():
                level[m, w] = counts[sel].mean()
    observed: dict[str, float] = {}
    expected: dict[str, float] = {}
    for day, name in holidays.items():
        i = (day - _EPOCH).days - lo
        if not 0 <= i < len(axis) or np.isnan(level[month[i], dow[i]]):
            continue
        observed[name] = observed.get(name, 0.0) + float(counts[i])
        expected[name] = expected.get(name, 0.0) + float(level[month[i], dow[i]])
    return {name: observed[name] / expected[name] for name in observed if expected[name] > 0}


def tail_index(
    values: Iterable[float] | npt.NDArray[np.float64],
    tail_fraction: float = 0.05,
    min_tail: int = 20,
) -> float | None:
    """The Hill estimator of the tail index ``alpha`` (``P(X > x)`` falls like ``x ** -alpha``)
    from the largest ``tail_fraction`` of the positive values, at least ``min_tail`` of them.
    ``None`` when there are too few positive values. A pure Pareto sample gives its shape; a
    thinner tail (a normal, an exponential) gives a larger index that grows with the threshold."""
    x = np.asarray(list(values) if not isinstance(values, np.ndarray) else values, dtype=np.float64)
    x = x[np.isfinite(x) & (x > 0)]
    k = max(min_tail, int(tail_fraction * len(x)))
    if len(x) < k + 1:
        return None
    top = np.partition(x, len(x) - k - 1)[len(x) - k - 1 :]  # the k + 1 largest, unordered
    top.sort()
    threshold = top[0]
    spread = float(np.log(top[1:] / threshold).mean())
    return None if spread <= 0 else 1.0 / spread
