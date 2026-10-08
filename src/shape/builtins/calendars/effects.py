"""Calendar effects: events with ramp-up and decay, paydays, period ends and trends.

Each effect turns a date range into one multiplicative factor per day (1.0 means no change; below
1.0 is a dip, 0 a closed day). Every factor depends on the date alone, never on the range asked
for, so any sub-range is the slice of a longer one. Effects combine by multiplication.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np
import numpy.typing as npt

from .rules import Rule, rule_from_spec

Floats = npt.NDArray[np.float64]
SHAPES = ("linear", "exponential")


def _weight(distance: int, span: int, shape: str) -> float:
    """How much of an event's lift applies ``distance`` days (1 .. span) from its date."""
    x = distance / (span + 1.0)
    return 1.0 - x if shape == "linear" else math.exp(-3.0 * x)


def _check_lift(lift: float, what: str) -> float:
    lift = float(lift)
    if not math.isfinite(lift) or lift < 0:
        raise ValueError(f"{what}: lift must be a finite number >= 0 (a multiplier), got {lift}")
    return lift


@dataclass(frozen=True, slots=True)
class Event:
    """A named event: on each of its dates the day's factor is ``lift``; over the
    ``ramp_up_days`` before and the ``decay_days`` after, the lift fades in and out
    (``ramp`` and ``decay`` are ``linear`` or ``exponential``; the weight at distance ``j`` of
    ``S`` days is ``1 - j / (S + 1)`` or ``exp(-3 j / (S + 1))``). Overlapping events multiply.

    The dates come from ``rule`` (every year), from ``dates`` (one-offs), or both.
    """

    name: str
    lift: float
    rule: Rule | None = None
    dates: tuple[date, ...] = ()
    ramp_up_days: int = 0
    decay_days: int = 0
    ramp: str = "linear"
    decay: str = "linear"

    def __post_init__(self) -> None:
        _check_lift(self.lift, f"event {self.name!r}")
        if self.ramp_up_days < 0 or self.decay_days < 0:
            raise ValueError(f"event {self.name!r}: ramp_up_days and decay_days must be >= 0")
        if self.ramp not in SHAPES or self.decay not in SHAPES:
            raise ValueError(f"event {self.name!r}: ramp and decay must be one of {SHAPES}")
        if self.rule is None and not self.dates:
            raise ValueError(f"event {self.name!r} needs a rule or dates")

    def occurrences(self, start: date, end: date) -> list[date]:
        """The event's dates in ``[start, end]``."""
        found = {d for d in self.dates if start <= d <= end}
        if self.rule is not None:
            for year in range(start.year - 1, end.year + 2):
                d = self.rule.on(year)
                if d is not None and start <= d <= end:
                    found.add(d)
        return sorted(found)

    def factors(self, start: date, end: date) -> Floats:
        """One factor per day of ``[start, end]``."""
        n = (end - start).days + 1
        out = np.ones(n, dtype=np.float64)
        reach_before, reach_after = self.ramp_up_days, self.decay_days
        window_start = start - timedelta(days=reach_after)
        window_end = end + timedelta(days=reach_before)
        for d in self.occurrences(window_start, window_end):
            at = (d - start).days
            for k in range(-reach_before, reach_after + 1):
                i = at + k
                if not 0 <= i < n:
                    continue
                if k == 0:
                    w = 1.0
                elif k < 0:
                    w = _weight(-k, reach_before, self.ramp)
                else:
                    w = _weight(k, reach_after, self.decay)
                out[i] *= 1.0 + (self.lift - 1.0) * w
        return out


def _is_business_day(d: date) -> bool:
    return d.weekday() < 5


def _previous_business_day(d: date) -> date:
    while not _is_business_day(d):
        d -= timedelta(days=1)
    return d


@dataclass(frozen=True, slots=True)
class Payday:
    """Pay days: ``semimonthly`` (default ``days=(1, 15)``), ``monthly`` (default ``days=(28,)``;
    ``-1`` is the last day of the month) or ``biweekly`` (every 14 days from ``anchor``). With
    ``adjust="previous_business_day"`` a weekend pay day moves to the Friday before. The day's
    factor is ``lift``, fading in and out over ``ramp_up_days`` and ``decay_days`` like an
    :class:`Event`."""

    lift: float
    kind: str = "semimonthly"
    days: tuple[int, ...] | None = None  # (28,) for monthly, (1, 15) otherwise (#136)
    anchor: date = date(2000, 1, 7)  # a Friday
    adjust: str = "previous_business_day"
    ramp_up_days: int = 0
    decay_days: int = 0

    def __post_init__(self) -> None:
        _check_lift(self.lift, "payday")
        if self.kind not in ("semimonthly", "monthly", "biweekly"):
            raise ValueError("payday kind must be semimonthly, monthly or biweekly")
        if self.adjust not in ("previous_business_day", "none"):
            raise ValueError("payday adjust must be previous_business_day or none")

    @property
    def pay_days(self) -> tuple[int, ...]:
        """The days of the month it pays on (``days``, else the kind's default)."""
        if self.days is not None:
            return self.days
        return (28,) if self.kind == "monthly" else (1, 15)

    def _dates(self, start: date, end: date) -> list[date]:
        found: set[date] = set()
        if self.kind == "biweekly":
            first = (start - self.anchor).days // 14
            for k in range(first - 1, (end - self.anchor).days // 14 + 2):
                found.add(self.anchor + timedelta(days=14 * k))
        else:
            first_ym, last_ym = start.year * 12 + start.month - 1, end.year * 12 + end.month - 1
            for ym in range(first_ym - 1, last_ym + 2):
                year, month0 = divmod(ym, 12)
                month = month0 + 1
                last = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)).day
                for day in self.pay_days:
                    found.add(date(year, month, last if day == -1 else min(day, last)))
        if self.adjust == "previous_business_day":
            found = {_previous_business_day(d) for d in found}
        return sorted(found)

    def factors(self, start: date, end: date) -> Floats:
        margin = max(self.ramp_up_days, self.decay_days) + 40
        dates = tuple(self._dates(start - timedelta(days=margin), end + timedelta(days=margin)))
        event = Event("payday", self.lift, None, dates, self.ramp_up_days, self.decay_days)
        return event.factors(start, end)


@dataclass(frozen=True, slots=True)
class PeriodEnd:
    """The last ``days`` days of every month (``kind="month"``) or quarter (``"quarter"``) get
    the factor ``lift``."""

    lift: float
    kind: str = "month"
    days: int = 3

    def __post_init__(self) -> None:
        _check_lift(self.lift, "period end")
        if self.kind not in ("month", "quarter") or self.days < 1:
            raise ValueError("period end needs kind month or quarter and days >= 1")

    def factors(self, start: date, end: date) -> Floats:
        n = (end - start).days + 1
        out = np.ones(n, dtype=np.float64)
        ordinals = np.arange(start.toordinal(), start.toordinal() + n)
        months = np.array([date.fromordinal(int(o)).month for o in ordinals])
        years = np.array([date.fromordinal(int(o)).year for o in ordinals])
        month_end = np.array(
            [
                (date(y + (m == 12), m % 12 + 1, 1)).toordinal() - 1
                for y, m in zip(years, months, strict=True)
            ]
        )
        in_window = month_end - ordinals < self.days
        if self.kind == "quarter":
            in_window &= months % 3 == 0
        out[in_window] = self.lift
        return out


@dataclass(frozen=True, slots=True)
class Trend:
    """Slow change over time: compound ``annual_growth`` from ``origin``; ``steps`` (a date and a
    factor from that day on); ``ramps`` (start, end, factor: the level rises linearly from 1 at
    ``start`` to ``factor`` at ``end`` and stays)."""

    annual_growth: float = 0.0
    origin: date = date(2000, 1, 1)
    steps: tuple[tuple[date, float], ...] = ()
    ramps: tuple[tuple[date, date, float], ...] = ()

    def __post_init__(self) -> None:
        if not math.isfinite(self.annual_growth) or self.annual_growth <= -1.0:
            raise ValueError(
                f"annual_growth must be a finite number above -1, got {self.annual_growth}"
            )
        for _, f in self.steps:
            _check_lift(f, "trend step")
        for s, e, f in self.ramps:
            _check_lift(f, "trend ramp")
            if e < s:
                raise ValueError("a trend ramp must not end before it starts")

    def factors(self, start: date, end: date) -> Floats:
        n = (end - start).days + 1
        days = np.arange(n, dtype=np.float64) + (start - self.origin).days
        out = (1.0 + self.annual_growth) ** (days / 365.25)
        for d, f in self.steps:
            out = np.where(days >= (d - self.origin).days, out * f, out)
        for s, e, f in self.ramps:
            a, b = (s - self.origin).days, (e - self.origin).days
            frac = np.clip((days - a) / max(b - a, 1), 0.0, 1.0)
            out = out * (1.0 + (f - 1.0) * frac)
        return out


def event_from_spec(spec: Mapping[str, Any]) -> Event:
    """An event from a mapping: ``name``, ``lift``, and its dates as ``date`` / ``dates`` (ISO) or
    a ``rule`` (see :func:`shape.builtins.calendars.rules.rule_from_spec`); optional
    ``ramp_up_days``, ``decay_days``, ``ramp``, ``decay``."""
    dates = tuple(date.fromisoformat(str(d)) for d in spec.get("dates", ()))
    if spec.get("date") is not None:
        dates += (date.fromisoformat(str(spec["date"])),)
    rule = rule_from_spec(spec["rule"]) if spec.get("rule") is not None else None
    return Event(
        str(spec.get("name", "event")),
        float(spec.get("lift", 1.0)),
        rule,
        dates,
        int(spec.get("ramp_up_days", 0)),
        int(spec.get("decay_days", 0)),
        str(spec.get("ramp", "linear")),
        str(spec.get("decay", "linear")),
    )


def payday_from_spec(spec: Mapping[str, Any]) -> Payday:
    days = spec.get("days")
    kwargs: dict[str, Any] = {}
    if days is not None:
        kwargs["days"] = tuple(int(d) for d in days)
    if spec.get("anchor") is not None:
        kwargs["anchor"] = date.fromisoformat(str(spec["anchor"]))
    return Payday(
        float(spec.get("lift", 1.0)),
        str(spec.get("kind", "semimonthly")),
        adjust=str(spec.get("adjust", "previous_business_day")),
        ramp_up_days=int(spec.get("ramp_up_days", 0)),
        decay_days=int(spec.get("decay_days", 0)),
        **kwargs,
    )


def trend_from_spec(spec: Mapping[str, Any]) -> Trend:
    def day(v: Any) -> date:
        return date.fromisoformat(str(v))

    steps: Sequence[Mapping[str, Any]] = spec.get("steps", ())
    ramps: Sequence[Mapping[str, Any]] = spec.get("ramps", ())
    return Trend(
        float(spec.get("annual_growth", 0.0)),
        day(spec["origin"]) if spec.get("origin") else date(2000, 1, 1),
        tuple((day(s["date"]), float(s["factor"])) for s in steps),
        tuple((day(r["start"]), day(r["end"]), float(r["factor"])) for r in ramps),
    )


__all__ = [
    "SHAPES",
    "Event",
    "Payday",
    "PeriodEnd",
    "Trend",
    "event_from_spec",
    "payday_from_spec",
    "trend_from_spec",
]
