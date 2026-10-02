"""The virtual clock: integer microseconds since 1970-01-01 UTC, and durations in it."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

US = 1_000_000
UNIT_US: dict[str, int] = {
    "seconds": US,
    "minutes": 60 * US,
    "hours": 3600 * US,
    "days": 86400 * US,
    "weeks": 7 * 86400 * US,
    "months": 2_629_800 * US,  # 30.4375 days
    "years": 31_557_600 * US,  # 365.25 days
}
_ALIASES = {u[:-1]: u for u in UNIT_US} | {u: u for u in UNIT_US}
_EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
_DURATION = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([a-z]+)\s*$")


def unit_us(unit: str) -> int:
    """Microseconds in one ``unit`` (singular or plural, any case)."""
    key = _ALIASES.get(str(unit).lower())
    if key is None:
        raise ValueError(f"unknown time unit {unit!r}; use one of {', '.join(UNIT_US)}")
    return UNIT_US[key]


def to_us(value: Any) -> int:
    """A time as microseconds since the epoch: an int (already microseconds), a ``date``, a
    ``datetime`` (naive means UTC) or an ISO-8601 string."""
    if isinstance(value, bool):
        raise TypeError("a time cannot be a bool")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            value = dt.datetime.fromisoformat(text)
        except ValueError:
            raise ValueError(f"not an ISO-8601 date or time: {value!r}") from None
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        delta = value - _EPOCH
    elif isinstance(value, dt.date):
        delta = dt.datetime(value.year, value.month, value.day, tzinfo=dt.timezone.utc) - _EPOCH
    else:
        raise TypeError(f"cannot read {type(value).__name__} as a time")
    return (delta.days * 86400 + delta.seconds) * US + delta.microseconds


def from_us(us: int) -> dt.datetime:
    """The UTC ``datetime`` at ``us`` microseconds since the epoch."""
    return _EPOCH + dt.timedelta(microseconds=int(us))


def add_years(us: int, years: float) -> int:
    """``us`` plus ``years``: whole years move the calendar year (29 February goes to 28
    February), a fractional part adds 365.25-day years."""
    whole = int(years)
    frac = years - whole
    d = from_us(us)
    if whole:
        try:
            d = d.replace(year=d.year + whole)
        except ValueError:
            d = d.replace(year=d.year + whole, day=28)
    out = to_us(d)
    return out + round(frac * UNIT_US["years"])


def parse_duration(text: str) -> int:
    """``"7 days"`` as microseconds."""
    m = _DURATION.match(str(text).lower())
    if not m:
        raise ValueError(f"not a duration like '7 days': {text!r}")
    return round(float(m.group(1)) * unit_us(m.group(2)))
