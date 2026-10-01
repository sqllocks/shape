"""Timestamps from a temporal profile: month, day-of-week and hour-of-day profiles, a calendar
of lifts, and a date range (stable interface; ``docs/GENERATION_CALENDARS.md``).

:func:`sample_timestamps` is row addressed: the timestamp of row ``r`` is a function of the
stream and ``r`` alone. It is the generation half of the profile round trip
(:func:`shape.profile.temporal.temporal_profile` is the other half): the month, day-of-week and
hour distributions of what it generates are the weights it was given.

A day's weight is ``month_w[month] * dow_w[weekday]`` divided by the number of days in the range
that share its (month, weekday) pair, so each pair carries its own weight however many days it
has, times the calendar's lift for that day. A day is drawn by alias sampling, then an hour from
``hour`` (or Gaussian ``hour_peaks``), then a uniform instant inside the hour.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]

from shape.generation.arrowkit import array as arrow_array
from shape.generation.arrowkit import to_numpy as arrow_numpy

from . import kernel_ops
from .rng import RowStream

EPOCH = date(1970, 1, 1)


def _weights(values: Sequence[float] | None, size: int, what: str) -> list[float]:
    if values is None:
        return [1.0] * size
    w = [float(v) for v in values]
    if len(w) != size:
        raise ValueError(f"{what} needs {size} weights, got {len(w)}")
    if any(v < 0 or v != v for v in w) or sum(w) <= 0:
        raise ValueError(f"{what} weights must be non-negative with a positive sum")
    total = sum(w)
    return [v / total for v in w]


def _calendar_lift(calendar: Any, start: date, end: date) -> np.ndarray[Any, np.dtype[np.float64]]:
    if calendar is None:
        return np.ones((end - start).days + 1, dtype=np.float64)
    if isinstance(calendar, Mapping):
        from shape.plugins.host import default_host

        calendar = default_host().get("shape.calendars", "composite").with_spec(calendar)
    lift = np.asarray(arrow_numpy(calendar.lift(start, end)), dtype=np.float64)
    if len(lift) != (end - start).days + 1:
        raise ValueError("the calendar returned a lift for the wrong number of days")
    return lift


def day_probabilities(
    start: date,
    end: date,
    *,
    month: Sequence[float] | None = None,
    day_of_week: Sequence[float] | None = None,
    calendar: Any = None,
    per_bucket: bool = True,
) -> pa.Array:
    """The (unnormalised) weight of each day of ``[start, end]``."""
    if end < start:
        raise ValueError("end must not be before start")
    days = (end - start).days + 1
    base = np.asarray(
        arrow_numpy(
            kernel_ops.day_weights(
                (start - EPOCH).days,
                days,
                _weights(month, 12, "month"),
                _weights(day_of_week, 7, "day_of_week"),
                per_bucket,
            )
        )
    )
    return arrow_array(base * _calendar_lift(calendar, start, end))


def sample_timestamps(
    stream: RowStream,
    row_start: int,
    n_rows: int,
    start: date,
    end: date,
    *,
    month: Sequence[float] | None = None,
    day_of_week: Sequence[float] | None = None,
    hour: Sequence[float] | None = None,
    hour_peaks: tuple[Sequence[float], float] | None = None,
    calendar: Any = None,
    per_bucket: bool = True,
    whole_seconds: bool = False,
) -> pa.Array:
    """``timestamp[us]`` values for rows ``row_start ..``, between ``start`` and ``end``
    (inclusive days).

    ``month`` (12 weights, January first), ``day_of_week`` (7, Monday first) and ``hour`` (24)
    default to uniform; ``hour_peaks=(peaks, std)`` is a mixture of Gaussian peaks (in hours) in
    place of ``hour``. ``calendar`` is an object with ``lift(start, end)`` or a spec mapping (see
    :func:`shape.builtins.calendars.calendar_from_spec`). ``whole_seconds`` drops the
    sub-second part.
    """
    if hour is not None and hour_peaks is not None:
        raise ValueError("give hour or hour_peaks, not both")
    if hour_peaks is not None:
        hour_w = kernel_ops.hour_weights_peaks(list(hour_peaks[0]), float(hour_peaks[1]))
    else:
        hour_w = arrow_array(_weights(hour, 24, "hour"), type=pa.float64())
    day_w = day_probabilities(
        start, end, month=month, day_of_week=day_of_week, calendar=calendar, per_bucket=per_bucket
    )
    if n_rows == 0:
        return arrow_array([], type=pa.timestamp("us"))
    return kernel_ops.temporal_sample(
        day_w, hour_w, (start - EPOCH).days, stream, row_start, n_rows, whole_seconds
    )
