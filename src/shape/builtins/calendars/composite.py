"""Calendars as code: the holiday calendars and every other effect combined.

:class:`RuleCalendar` is a set of named holiday rules with a lift each (the US federal and US
retail calendars are two). :class:`CompositeCalendar` multiplies any number of components:
rule calendars, events, paydays, period ends, trends and other ``shape.calendars`` plugins.
:func:`calendar_from_spec` builds one from a mapping, so a generator spec can describe it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]

from shape.generation.arrowkit import array as arrow_array
from shape.generation.arrowkit import to_numpy as arrow_numpy

from .effects import (
    Event,
    Payday,
    PeriodEnd,
    Trend,
    event_from_spec,
    payday_from_spec,
    trend_from_spec,
)
from .rules import Rule

Floats = npt.NDArray[np.float64]


class _Component(Protocol):
    def factors(self, start: date, end: date) -> Floats: ...


def _check_range(start: date, end: date) -> None:
    if end < start:
        raise ValueError("end must not be before start")


class RuleCalendar:
    """Holidays from rules. Each holiday has a lift (``lifts`` by name, else ``holiday_lift``;
    1.0 is neutral) with optional ramp-up and decay around it. ``holidays(start, end)`` lists the
    dates. Subclasses set ``rules`` (name to :class:`Rule`), ``from_year`` (a holiday that began
    in a given year) and ``shift`` (the observed-day rule)."""

    name = ""
    rules: Mapping[str, Rule] = {}
    from_year: Mapping[str, int] = {}
    shift: Callable[[date], date] = staticmethod(lambda d: d)

    def __init__(
        self,
        holiday_lift: float = 1.0,
        lifts: Mapping[str, float] | None = None,
        ramp_up_days: int = 0,
        decay_days: int = 0,
        ramp: str = "linear",
        decay: str = "linear",
    ) -> None:
        self.holiday_lift = float(holiday_lift)
        self.lifts = dict(lifts or {})
        unknown = sorted(set(self.lifts) - set(self.rules))
        if unknown:
            raise ValueError(f"unknown holidays {unknown}; known: {sorted(self.rules)}")
        self.ramp_up_days, self.decay_days = int(ramp_up_days), int(decay_days)
        self._events = [
            Event(
                label,
                float(self.lifts.get(label, self.holiday_lift)),
                _Shifted(rule, self.shift, self.from_year.get(label, 0)),
                (),
                self.ramp_up_days,
                self.decay_days,
                ramp,
                decay,
            )
            for label, rule in self.rules.items()
        ]

    def holidays(self, start: date, end: date) -> dict[date, str]:
        """Holiday dates (observed dates where the calendar shifts them) in ``[start, end]``."""
        found: dict[date, str] = {}
        for event in self._events:
            for d in event.occurrences(start, end):
                found.setdefault(d, event.name)
        return found

    def factors(self, start: date, end: date) -> Floats:
        out = np.ones((end - start).days + 1, dtype=np.float64)
        for event in self._events:
            out *= event.factors(start, end)
        return out

    def lift(self, start: date, end: date) -> pa.Array:
        _check_range(start, end)
        return arrow_array(self.factors(start, end))


class _Shifted:
    """A rule with the calendar's observed-day shift and first year applied."""

    def __init__(self, base: Rule, shift: Callable[[date], date], from_year: int) -> None:
        self.base, self.shift, self.from_year = base, shift, from_year

    def on(self, year: int) -> date | None:
        if year < self.from_year:
            return None
        d = self.base.on(year)
        return None if d is None else self.shift(d)


class CompositeCalendar:
    """The product of its components' factors. Create it empty (neutral) or from a spec with
    :meth:`with_spec`; the ``shape.calendars`` entry ``composite`` is this class."""

    name = "composite"

    def __init__(
        self,
        components: Sequence[_Component] = (),
        plugins: Sequence[Any] = (),
    ) -> None:
        self.components = list(components)
        self.plugins = list(plugins)

    def with_spec(self, spec: Mapping[str, Any]) -> CompositeCalendar:
        return calendar_from_spec(spec)

    def factors(self, start: date, end: date) -> Floats:
        _check_range(start, end)
        out = np.ones((end - start).days + 1, dtype=np.float64)
        for component in self.components:
            out *= component.factors(start, end)
        for plugin in self.plugins:
            out *= np.asarray(arrow_numpy(plugin.lift(start, end)))
        return out

    def lift(self, start: date, end: date) -> pa.Array:
        return arrow_array(self.factors(start, end))


def _named_calendar(name: str, options: Mapping[str, Any]) -> Any:
    from shape.plugins.host import default_host

    found = default_host().try_get("shape.calendars", name)
    if found is None:
        raise ValueError(f"unknown calendar {name!r}")
    if options and hasattr(type(found), "__init__"):
        try:
            return type(found)(**options)
        except TypeError as exc:
            raise ValueError(f"calendar {name!r} does not take those options: {exc}") from exc
    return found


def calendar_from_spec(spec: Mapping[str, Any]) -> CompositeCalendar:
    """A calendar from a mapping with any of these keys:

    * ``calendars``: names of ``shape.calendars`` (``"us_federal"``, ``"us_retail"``, a plugin's),
      or ``{"name": ..., "holiday_lift": ..., "lifts": {...}, "ramp_up_days": ..., ...}``;
    * ``events``: custom events, see :func:`~.effects.event_from_spec`;
    * ``payday``: a payday mapping (or a list of them), see :func:`~.effects.payday_from_spec`;
    * ``month_end`` / ``quarter_end``: ``{"lift": 1.2, "days": 3}``;
    * ``trend``: ``{"annual_growth": 0.1, "steps": [...], "ramps": [...]}``.
    """
    components: list[_Component] = []
    plugins: list[Any] = []
    for entry in spec.get("calendars", ()):
        if isinstance(entry, str):
            plugins.append(_named_calendar(entry, {}))
        else:
            options = {k: v for k, v in entry.items() if k != "name"}
            plugins.append(_named_calendar(str(entry["name"]), options))
    components += [event_from_spec(e) for e in spec.get("events", ())]
    payday = spec.get("payday")
    if payday:
        paydays = payday if isinstance(payday, list) else [payday]
        components += [payday_from_spec(p) for p in paydays]
    for key, kind in (("month_end", "month"), ("quarter_end", "quarter")):
        if spec.get(key):
            p = spec[key]
            components.append(PeriodEnd(float(p.get("lift", 1.0)), kind, int(p.get("days", 3))))
    if spec.get("trend"):
        components.append(trend_from_spec(spec["trend"]))
    return CompositeCalendar(components, plugins)


__all__ = [
    "CompositeCalendar",
    "Event",
    "Payday",
    "PeriodEnd",
    "RuleCalendar",
    "Trend",
    "calendar_from_spec",
]
