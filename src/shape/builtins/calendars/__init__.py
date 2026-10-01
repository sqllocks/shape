"""Built-in US calendars (``shape.calendars``): federal and retail holidays, rule-based (D-11).

Dates come from rules, not tables: fixed dates, the nth or last weekday of a month, Easter by
the Gregorian computus and (federal only) the observed-day shift. A day's lift is
``holiday_lift`` on a holiday and ``1.0`` otherwise. The default ``holiday_lift`` is 1.0
(no change): lift magnitudes, ramps and decay belong to the generation engine (P4-05). Use
:meth:`holidays` for the dates themselves.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]

SHAPE_API = "1.0"

_MON, _THU, _FRI, _SUN = 0, 3, 4, 6


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The ``n``-th ``weekday`` (Monday is 0) of the month; ``n == -1`` means the last."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def easter(year: int) -> date:
    """Easter Sunday (anonymous Gregorian computus)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    el = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * el) // 451
    month = (h + el - 7 * m + 114) // 31
    day = (h + el - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def observed(d: date) -> date:
    """The weekday a federal holiday is observed: Saturday on Friday, Sunday on Monday."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


Rule = Callable[[int], date]

_FEDERAL: dict[str, Rule] = {
    "new_years_day": lambda y: date(y, 1, 1),
    "martin_luther_king_day": lambda y: nth_weekday(y, 1, _MON, 3),
    "washingtons_birthday": lambda y: nth_weekday(y, 2, _MON, 3),
    "memorial_day": lambda y: nth_weekday(y, 5, _MON, -1),
    "juneteenth": lambda y: date(y, 6, 19),
    "independence_day": lambda y: date(y, 7, 4),
    "labor_day": lambda y: nth_weekday(y, 9, _MON, 1),
    "columbus_day": lambda y: nth_weekday(y, 10, _MON, 2),
    "veterans_day": lambda y: date(y, 11, 11),
    "thanksgiving": lambda y: nth_weekday(y, 11, _THU, 4),
    "christmas_day": lambda y: date(y, 12, 25),
}
_FEDERAL_FROM = {"juneteenth": 2021}  # first year it is a federal holiday

_RETAIL: dict[str, Rule] = {
    "new_years_day": lambda y: date(y, 1, 1),
    "valentines_day": lambda y: date(y, 2, 14),
    "easter": easter,
    "mothers_day": lambda y: nth_weekday(y, 5, _SUN, 2),
    "fathers_day": lambda y: nth_weekday(y, 6, _SUN, 3),
    "thanksgiving": lambda y: nth_weekday(y, 11, _THU, 4),
    "black_friday": lambda y: nth_weekday(y, 11, _THU, 4) + timedelta(days=1),
    "cyber_monday": lambda y: nth_weekday(y, 11, _THU, 4) + timedelta(days=4),
    "christmas_eve": lambda y: date(y, 12, 24),
    "christmas_day": lambda y: date(y, 12, 25),
}


class _RuleCalendar:
    name = ""
    _rules: dict[str, Rule] = {}
    _shift = staticmethod(lambda d: d)
    _from: dict[str, int] = {}

    def __init__(self, holiday_lift: float = 1.0) -> None:
        self.holiday_lift = float(holiday_lift)

    def holidays(self, start: date, end: date) -> dict[date, str]:
        """Holiday dates (observed dates for the federal calendar) in ``[start, end]``."""
        found: dict[date, str] = {}
        for year in range(start.year - 1, end.year + 2):
            for label, rule in self._rules.items():
                if year < self._from.get(label, 0):
                    continue
                day = self._shift(rule(year))
                if start <= day <= end:
                    found.setdefault(day, label)
        return found

    def lift(self, start: date, end: date) -> pa.Array:
        if end < start:
            raise ValueError("end must not be before start")
        factors = np.ones((end - start).days + 1, dtype=np.float64)
        for day in self.holidays(start, end):
            factors[(day - start).days] = self.holiday_lift
        return pa.array(factors)


class UsFederalCalendar(_RuleCalendar):
    """The eleven US federal holidays, on their observed weekdays."""

    name = "us_federal"
    _rules = _FEDERAL
    _shift = staticmethod(observed)
    _from = _FEDERAL_FROM


class UsRetailCalendar(_RuleCalendar):
    """Retail peaks: Black Friday, Cyber Monday, Christmas Eve and the gift holidays."""

    name = "us_retail"
    _rules = _RETAIL


__all__ = ["SHAPE_API", "UsFederalCalendar", "UsRetailCalendar", "easter", "nth_weekday"]
