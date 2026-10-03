"""Built-in strategy ``temporal``: timestamps, uniform over a date range or with seasonal
profiles. Row addressed (``docs/GENERATION_STRATEGIES.md``); the day, hour and time of day come
from the generation kernel's temporal sampler (five words per row)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]

from shape.generation import kernel_ops
from shape.generation.arrowkit import array as arrow_array
from shape.generation.arrowkit import to_numpy as arrow_numpy
from shape.generation.strategy_kit import StrategyError, stream, where
from shape.plugins.api.v1 import GenerationContext

SHAPE_API = "1.0"

_DAY_US = 86_400_000_000
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_DOW = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _microseconds(text: Any, what: str, ctx: GenerationContext) -> int:
    try:
        parsed = datetime.fromisoformat(str(text))
    except ValueError as exc:
        raise StrategyError(f"temporal {what} {text!r} is not an ISO date ({where(ctx)})") from exc
    return int(np.datetime64(parsed, "us").astype(np.int64))


def _date_only(text: Any) -> bool:
    """Whether ``text`` names a day (``2026-05-01``), not an instant."""
    raw = str(text).strip()
    return len(raw) <= 10 and "T" not in raw and ":" not in raw


class _Range:
    """The microsecond range of a column. ``end`` is the bound as written; ``inclusive`` says it
    was a date, which stands for the whole day, so the end day is a possible day (a one-day range
    has ``start == end``)."""

    __slots__ = ("end", "end_text", "inclusive", "start", "start_text")

    def __init__(self, start: int, end: int, start_text: Any, end_text: Any) -> None:
        self.start, self.end = start, end
        self.start_text, self.end_text = start_text, end_text
        self.inclusive = _date_only(end_text)

    @property
    def stop(self) -> int:
        """The exclusive upper bound in microseconds."""
        return self.end + _DAY_US if self.inclusive else self.end

    def check_days(self, n_days: int, ctx: GenerationContext) -> None:
        """A day-weighted range needs at least one day (the end day counts)."""
        if n_days < 1:
            self.check(ctx)
            raise StrategyError(f"temporal range has no day ({where(ctx)})")

    def check(self, ctx: GenerationContext) -> None:
        if self.end < self.start:
            raise StrategyError(
                f"temporal end {self.end_text} is before the start {self.start_text} ({where(ctx)})"
            )
        if self.stop <= self.start:
            raise StrategyError(
                f"temporal end equals the start {self.start_text} and the end is an exclusive "
                f"time: give dates (end {str(self.end_text)[:10]} is then a possible day) "
                f"({where(ctx)})"
            )


def _range(spec: Mapping[str, Any], ctx: GenerationContext) -> _Range:
    """``range_ref: model.date_range`` takes the model's range; otherwise a nested ``date_range``
    (or ``range``), then top-level ``start``/``end``, then 2022-01-01 .. 2025-12-31."""
    if spec.get("range_ref") == "model.date_range":
        engine = getattr(ctx, "engine", None)
        window: Mapping[str, Any] = engine.schema.model.date_range if engine is not None else {}
    else:
        window = spec.get("date_range", spec.get("range", {})) or {}
    start = window.get("start")
    end = window.get("end")
    start = spec.get("start", "2022-01-01") if start is None else start
    end = spec.get("end", "2025-12-31") if end is None else end
    return _Range(_microseconds(start, "start", ctx), _microseconds(end, "end", ctx), start, end)


_UNITS = ("s", "ms", "us", "ns")


def _uniform(window: _Range, ctx: GenerationContext) -> npt.NDArray[np.int64]:
    """Microsecond timestamps uniform on ``[start, stop)``: a date end is a possible day."""
    window.check(ctx)
    start, end = window.start, window.stop
    u = stream(ctx, "v").uniform(ctx.row_start, ctx.n_rows)
    offsets = np.minimum((u * (end - start)).astype(np.int64), end - start - 1)
    return start + offsets


def _weights(profile: Mapping[str, Any], names: tuple[str, ...]) -> npt.NDArray[np.float64]:
    base = 1.0 / len(names)
    w = np.array([float(profile.get(n, base)) for n in names], dtype=np.float64)
    if (w < 0).any() or not np.isfinite(w).all() or w.sum() <= 0:
        raise StrategyError("temporal profile weights must be non-negative with a positive sum")
    out: npt.NDArray[np.float64] = w / w.sum()
    return out


def _day_weights(
    first_day: int, n_days: int, month_p: npt.NDArray[np.float64], dow_p: npt.NDArray[np.float64]
) -> npt.NDArray[np.float64]:
    """Probability of each day of a range: a (month, weekday) bucket has probability
    ``month_p * dow_p`` shared equally by the days of the range in it. The probability of buckets
    with no day in the range is spread over every day but the last."""
    days = np.arange(first_day, first_day + n_days, dtype=np.int64)
    months = days.astype("datetime64[D]").astype("datetime64[M]").astype(np.int64) % 12
    dows = (days + 3) % 7
    bucket = month_p[:, None] * dow_p[None, :]
    count = np.zeros((12, 7), dtype=np.float64)
    np.add.at(count, (months, dows), 1.0)
    w = bucket[months, dows] / count[months, dows]
    empty = float(bucket[count == 0].sum())
    if empty > 0.0:
        spread = np.ones(n_days)
        if n_days > 1:
            spread[-1] = 0.0
        w = w + empty * spread / spread.sum()
    return w


class Temporal:
    """Timestamps (``timestamp[us]``, or the ``unit`` asked for: ``s``, ``ms``, ``us`` or ``ns``;
    the values are the same instants whatever the unit).

    The range is ``date_range`` (or ``range``) ``{"start", "end"}``, or top-level ``start`` and
    ``end``, or ``range_ref: "model.date_range"``; the default is 2022-01-01 .. 2025-12-31.
    ``pattern: "uniform"`` (the default, and what any unknown pattern means) draws uniformly on
    ``[start, end)``. An ``end`` that is a date (``2026-05-01``) stands for the whole day, so the
    end day is a possible day for every pattern and ``start == end`` is one single day; an ``end``
    with a time of day is the exact, exclusive bound. ``pattern: "seasonal"`` weights days by
    ``profiles.month`` (``Jan`` ..
    ``Dec``) and ``profiles.day_of_week`` (``Mon`` .. ``Sun``; missing names weigh 1/12 and 1/7,
    and ``month_weights`` / ``day_of_week_weights`` at the top level are accepted too); the end
    date itself is a possible day. ``profiles.hour_of_day`` replaces the time of day by a whole
    second of an hour drawn uniformly, with one weight per hour (keys ``"0"`` .. ``"23"``), or with
    ``{"distribution": "bimodal", "peaks": [12, 18],
    "std_dev": 2}`` from equally likely Gaussian peaks wrapped around midnight. Without it the
    time of day is uniform.
    """

    name = "temporal"
    generator_version = 1

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        values = self._microseconds(spec, ctx)
        granularity = spec.get("granularity")
        if granularity == "day":
            micros = values.cast(pa.int64()).to_numpy(zero_copy_only=False)
            values = arrow_array((micros // _DAY_US) * _DAY_US, type=pa.int64()).cast(
                pa.timestamp("us")
            )
        elif granularity is not None:
            raise StrategyError(
                f"temporal 'granularity' must be 'day', not {granularity!r} ({where(ctx)})"
            )
        unit = str(spec.get("unit", "us"))
        if unit == "us":
            return values
        if unit not in _UNITS:
            raise StrategyError(
                f"temporal 'unit' must be one of {', '.join(_UNITS)}, not {unit!r} ({where(ctx)})"
            )
        try:
            return values.cast(pa.timestamp(unit))
        except pa.ArrowInvalid as exc:
            raise StrategyError(f"temporal values do not fit unit {unit!r} ({where(ctx)})") from exc

    def _microseconds(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        window = _range(spec, ctx)
        start, end = window.start, window.end
        if spec.get("pattern", "uniform") != "seasonal":
            return arrow_array(
                _uniform(window, ctx).astype("datetime64[us]"), type=pa.timestamp("us")
            )
        profiles = dict(spec.get("profiles") or {})
        if not profiles.get("month") and spec.get("month_weights"):
            profiles["month"] = spec["month_weights"]
        if not profiles.get("day_of_week") and spec.get("day_of_week_weights"):
            profiles["day_of_week"] = spec["day_of_week_weights"]
        month_w, dow_w = profiles.get("month") or {}, profiles.get("day_of_week") or {}
        hour_profile = profiles.get("hour_of_day") or {}
        hours = self._hours(hour_profile, ctx)
        if not month_w and not dow_w:
            if not hour_profile:
                return arrow_array(
                    _uniform(window, ctx).astype("datetime64[us]"), type=pa.timestamp("us")
                )
            first = start // _DAY_US
            n_days = -(-window.stop // _DAY_US) - first
            window.check_days(n_days, ctx)
            days = np.ones(n_days)
        else:
            first = start // _DAY_US
            n_days = end // _DAY_US - first + 1
            window.check_days(n_days, ctx)
            days = _day_weights(first, n_days, _weights(month_w, _MONTHS), _weights(dow_w, _DOW))
        return kernel_ops.temporal_sample(
            arrow_array(days),
            arrow_array(hours),
            first,
            stream(ctx, "t"),
            ctx.row_start,
            ctx.n_rows,
            whole_seconds=bool(hour_profile),
        )

    @staticmethod
    def _hours(profile: Mapping[str, Any], ctx: GenerationContext) -> npt.NDArray[np.float64]:
        if profile.get("distribution") == "bimodal":
            peaks = [float(p) for p in profile.get("peaks", [12, 18])]
            std = float(profile.get("std_dev", 2))
            if not peaks or not std > 0:
                raise StrategyError(
                    f"temporal bimodal hours need peaks and a positive std_dev ({where(ctx)})"
                )
            return np.asarray(
                arrow_numpy(kernel_ops.hour_weights_peaks(peaks, std)),
                dtype=np.float64,
            )
        if profile and all(str(k).isdigit() for k in profile):
            # one weight per hour of the day (a profile's hour histogram): "0" .. "23"
            w = np.array([float(profile.get(str(h), 0.0)) for h in range(24)], dtype=np.float64)
            if any(int(k) > 23 for k in profile):
                raise StrategyError(f"temporal hour_of_day keys are hours 0..23 ({where(ctx)})")
            if (w < 0).any() or not np.isfinite(w).all() or w.sum() <= 0:
                raise StrategyError("temporal hour_of_day weights must be non-negative, not all 0")
            return w
        return np.ones(24)


__all__ = ["SHAPE_API", "Temporal"]
