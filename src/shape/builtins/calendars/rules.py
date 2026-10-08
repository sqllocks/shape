"""The date rule engine (D-11): holidays as code, offline and deterministic.

A rule maps a year to a date (or ``None``): a fixed month and day, the nth or last weekday of a
month, Easter by the Gregorian computus (plus or minus days), a date relative to another rule,
and the observed-day shift (a Saturday holiday is taken on the Friday, a Sunday one on the
Monday). :func:`rule_from_spec` builds one from a JSON-friendly mapping.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Protocol

MON, TUE, WED, THU, FRI, SAT, SUN = range(7)
_WEEKDAYS = {n: i for i, n in enumerate(("mon", "tue", "wed", "thu", "fri", "sat", "sun"))}


class Rule(Protocol):
    def on(self, year: int) -> date | None: ...


def _check_n(n: int) -> int:
    if not (1 <= n <= 5 or n == -1):
        raise ValueError(f"n must be 1 to 5, or -1 for the last, got {n}")
    return n


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The ``n``-th ``weekday`` (Monday is 0) of the month; ``n == -1`` means the last. A fifth
    weekday the month does not have falls in the next month: :class:`NthWeekday` checks it."""
    _check_n(n)
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
    if d.weekday() == SAT:
        return d - timedelta(days=1)
    if d.weekday() == SUN:
        return d + timedelta(days=1)
    return d


@dataclass(frozen=True, slots=True)
class FixedDate:
    """The same month and day every year, optionally only from ``from_year`` / until
    ``until_year``. February 29 exists only in leap years."""

    month: int
    day: int
    from_year: int = 0
    until_year: int = 9999

    def on(self, year: int) -> date | None:
        if not self.from_year <= year <= self.until_year:
            return None
        try:
            return date(year, self.month, self.day)
        except ValueError:
            return None


@dataclass(frozen=True, slots=True)
class NthWeekday:
    """The ``n``-th weekday of a month (``n`` 1 to 5, or -1 for the last). A year whose month has
    no ``n``-th such weekday (a fifth Monday) has no date (#135)."""

    month: int
    weekday: int
    n: int
    from_year: int = 0
    until_year: int = 9999

    def __post_init__(self) -> None:
        _check_n(self.n)

    def on(self, year: int) -> date | None:
        if not self.from_year <= year <= self.until_year:
            return None
        found = nth_weekday(year, self.month, self.weekday, self.n)
        return found if found.month == self.month else None


@dataclass(frozen=True, slots=True)
class EasterOffset:
    """Easter Sunday plus ``days`` (Good Friday is ``-2``, Easter Monday ``1``)."""

    days: int = 0

    def on(self, year: int) -> date | None:
        return easter(year) + timedelta(days=self.days)


@dataclass(frozen=True, slots=True)
class Offset:
    """Another rule's date plus ``days`` (Black Friday is Thanksgiving plus 1)."""

    base: Rule
    days: int

    def on(self, year: int) -> date | None:
        d = self.base.on(year)
        return None if d is None else d + timedelta(days=self.days)


@dataclass(frozen=True, slots=True)
class Observed:
    """Another rule's date moved to the weekday it is observed on."""

    base: Rule

    def on(self, year: int) -> date | None:
        d = self.base.on(year)
        return None if d is None else observed(d)


def weekday_number(value: Any) -> int:
    """A weekday as 0 (Monday) to 6 (Sunday), from a number or a name (``"thu"``)."""
    if isinstance(value, int):
        if not 0 <= value <= 6:
            raise ValueError(f"weekday must be 0..6, got {value}")
        return value
    key = str(value).strip().lower()[:3]
    if key not in _WEEKDAYS:
        raise ValueError(f"unknown weekday {value!r}")
    return _WEEKDAYS[key]


def rule_from_spec(spec: Mapping[str, Any]) -> Rule:
    """A rule from a mapping, one of:

    * ``{"month": 12, "day": 25}`` (optional ``from_year``, ``until_year``);
    * ``{"month": 11, "weekday": "thu", "n": 4}`` (``n`` 1 to 5, ``-1`` for the last; a year
      without that weekday has no date);
    * ``{"easter": 0}`` (days from Easter Sunday);
    * ``{"after": <rule>, "days": 1}`` (another rule's date plus days; negative for before);
    * ``{"observed": <rule>}``.
    """
    if "easter" in spec:
        return EasterOffset(int(spec["easter"]))
    if "after" in spec:
        return Offset(rule_from_spec(spec["after"]), int(spec.get("days", 0)))
    if "observed" in spec:
        return Observed(rule_from_spec(spec["observed"]))
    month = int(spec["month"])
    if not 1 <= month <= 12:
        raise ValueError(f"month must be 1..12, got {month}")
    years = {
        "from_year": int(spec.get("from_year", 0)),
        "until_year": int(spec.get("until_year", 9999)),
    }
    if "weekday" in spec:
        return NthWeekday(month, weekday_number(spec["weekday"]), int(spec.get("n", 1)), **years)
    return FixedDate(month, int(spec["day"]), **years)
