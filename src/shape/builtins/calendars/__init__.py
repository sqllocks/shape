"""Built-in calendars (``shape.calendars``): holidays as rules, and the effects around them.

Dates come from rules (:mod:`.rules`, D-11): fixed dates, the nth or last weekday of a month,
Easter by the Gregorian computus, offsets from another rule, and (federal only) the observed-day
shift. A day's lift is the holiday's ``lift`` on the day (``holiday_lift`` for all, ``lifts`` per
holiday), fading in over ``ramp_up_days`` and out over ``decay_days``; the default is 1.0, no
change. :mod:`.effects` adds custom events, paydays (the 1st and 15th, or biweekly), month-end and
quarter-end effects and trends, and :class:`~.composite.CompositeCalendar` combines them all.
``docs/GENERATION_CALENDARS.md`` has the details.
"""

from __future__ import annotations

from .composite import CompositeCalendar, RuleCalendar, calendar_from_spec
from .effects import Event, Payday, PeriodEnd, Trend
from .rules import (
    FRI,
    MON,
    SUN,
    THU,
    EasterOffset,
    FixedDate,
    NthWeekday,
    Observed,
    Offset,
    Rule,
    easter,
    nth_weekday,
    observed,
    rule_from_spec,
)

SHAPE_API = "1.0"

_FEDERAL: dict[str, Rule] = {
    "new_years_day": FixedDate(1, 1),
    "martin_luther_king_day": NthWeekday(1, MON, 3),
    "washingtons_birthday": NthWeekday(2, MON, 3),
    "memorial_day": NthWeekday(5, MON, -1),
    "juneteenth": FixedDate(6, 19),
    "independence_day": FixedDate(7, 4),
    "labor_day": NthWeekday(9, MON, 1),
    "columbus_day": NthWeekday(10, MON, 2),
    "veterans_day": FixedDate(11, 11),
    "thanksgiving": NthWeekday(11, THU, 4),
    "christmas_day": FixedDate(12, 25),
}

_THANKSGIVING = NthWeekday(11, THU, 4)
_RETAIL: dict[str, Rule] = {
    "new_years_day": FixedDate(1, 1),
    "valentines_day": FixedDate(2, 14),
    "easter": EasterOffset(0),
    "mothers_day": NthWeekday(5, SUN, 2),
    "fathers_day": NthWeekday(6, SUN, 3),
    "thanksgiving": _THANKSGIVING,
    "black_friday": Offset(_THANKSGIVING, 1),
    "cyber_monday": Offset(_THANKSGIVING, 4),
    "christmas_eve": FixedDate(12, 24),
    "christmas_day": FixedDate(12, 25),
}


class UsFederalCalendar(RuleCalendar):
    """The eleven US federal holidays, on their observed weekdays."""

    name = "us_federal"
    rules = _FEDERAL
    from_year = {"juneteenth": 2021}  # first year it is a federal holiday
    shift = staticmethod(observed)


class UsRetailCalendar(RuleCalendar):
    """Retail peaks: Black Friday, Cyber Monday, Christmas Eve and the gift holidays."""

    name = "us_retail"
    rules = _RETAIL


__all__ = [
    "FRI",
    "SHAPE_API",
    "CompositeCalendar",
    "EasterOffset",
    "Event",
    "FixedDate",
    "NthWeekday",
    "Observed",
    "Offset",
    "Payday",
    "PeriodEnd",
    "Rule",
    "RuleCalendar",
    "Trend",
    "UsFederalCalendar",
    "UsRetailCalendar",
    "calendar_from_spec",
    "easter",
    "nth_weekday",
    "observed",
    "rule_from_spec",
]
