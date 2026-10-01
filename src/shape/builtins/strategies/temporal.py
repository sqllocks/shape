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


def _range(spec: Mapping[str, Any], ctx: GenerationContext) -> tuple[int, int]:
    """``(start, end)`` in microseconds. ``range_ref: model.date_range`` takes the model's range;
    otherwise a nested ``date_range`` (or ``range``), then top-level ``start``/``end``, then
    2022-01-01 .. 2025-12-31."""
    if spec.get("range_ref") == "model.date_range":
        engine = getattr(ctx, "engine", None)
        window: Mapping[str, Any] = engine.schema.model.date_range if engine is not None else {}
    else:
        window = spec.get("date_range", spec.get("range", {})) or {}
    start = window.get("start")
    end = window.get("end")
    start = spec.get("start", "2022-01-01") if start is None else start
    end = spec.get("end", "2025-12-31") if end is None else end
    return _microseconds(start, "start", ctx), _microseconds(end, "end", ctx)


def _uniform(start: int, end: int, ctx: GenerationContext) -> npt.NDArray[np.int64]:
    """Microsecond timestamps uniform on ``[start, end)``."""
    if end <= start:
        raise StrategyError(f"temporal range must end after it starts ({where(ctx)})")
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
    """Timestamps (``timestamp[us]``).

    The range is ``date_range`` (or ``range``) ``{"start", "end"}``, or top-level ``start`` and
    ``end``, or ``range_ref: "model.date_range"``; the default is 2022-01-01 .. 2025-12-31.
    ``pattern: "uniform"`` (the default, and what any unknown pattern means) draws uniformly on
    ``[start, end)``. ``pattern: "seasonal"`` weights days by ``profiles.month`` (``Jan`` ..
    ``Dec``) and ``profiles.day_of_week`` (``Mon`` .. ``Sun``; missing names weigh 1/12 and 1/7,
    and ``month_weights`` / ``day_of_week_weights`` at the top level are accepted too); the end
    date itself is a possible day. ``profiles.hour_of_day`` replaces the time of day by a whole
    second of an hour drawn uniformly, or with ``{"distribution": "bimodal", "peaks": [12, 18],
    "std_dev": 2}`` from equally likely Gaussian peaks wrapped around midnight. Without it the
    time of day is uniform.
    """

    name = "temporal"

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        start, end = _range(spec, ctx)
        if spec.get("pattern", "uniform") != "seasonal":
            return pa.array(
                _uniform(start, end, ctx).astype("datetime64[us]"), type=pa.timestamp("us")
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
                return pa.array(
                    _uniform(start, end, ctx).astype("datetime64[us]"), type=pa.timestamp("us")
                )
            first = start // _DAY_US
            n_days = -(-end // _DAY_US) - first
            if n_days < 1:
                raise StrategyError(f"temporal range must end after it starts ({where(ctx)})")
            days = np.ones(n_days)
        else:
            first = start // _DAY_US
            n_days = end // _DAY_US - first + 1
            if n_days < 1:
                raise StrategyError(f"temporal range must end after it starts ({where(ctx)})")
            days = _day_weights(first, n_days, _weights(month_w, _MONTHS), _weights(dow_w, _DOW))
        return kernel_ops.temporal_sample(
            pa.array(days),
            pa.array(hours),
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
                kernel_ops.hour_weights_peaks(peaks, std).to_numpy(zero_copy_only=False),
                dtype=np.float64,
            )
        return np.ones(24)


__all__ = ["SHAPE_API", "Temporal"]
