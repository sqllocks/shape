"""pandas' string-to-datetime semantics for text columns, without pandas or dateutil.

``parse_mixed`` is ``pd.to_datetime(s, format="mixed", dayfirst=False)`` for one string, the call
the type inference uses. ``guess_format`` is ``pandas.tseries.api.guess_datetime_format``,
which ``pd.to_datetime(series, errors="coerce")`` (the call behind the date histograms) applies
to the first element before parsing the rest strictly. Both sit on ``_dateutil_parser``.

Zone-bearing text raises ``ZonedTextError``, a ``NotImplementedError`` (Shape does not model
tz-aware text columns).
"""

from __future__ import annotations

import calendar
import datetime as _dt
import re
from typing import Any

from . import _dateutil_parser as du


class ZonedTextError(NotImplementedError):
    """Date text with a time zone or UTC offset, which Shape does not model. Raised with the
    column's name once the profiler knows it, with the way to profile the column anyway."""

    def __init__(self, text: str, column: str | None = None) -> None:
        if column is None:
            msg = f"zone-bearing date text is not modelled: {text!r}"
        else:
            msg = (
                f"column {column!r} holds date text with a time zone or UTC offset ({text!r}), "
                "which the profiler does not model; parse it into a timestamp column first, for "
                f"example df[{column!r}] = pandas.to_datetime(df[{column!r}], utc=True), and "
                "profile the DataFrame"
            )
        super().__init__(msg)
        self.text, self.column = text, column

    def __reduce__(self) -> tuple[Any, ...]:  # a fork-pool worker sends it back pickled
        return type(self), (self.text, self.column)


_NAT = object()  # the text pandas turns into NaT without error
NAT_STRINGS = {"NaT", "nat", "NAT", "nan", "NaN", "NAN"}
_NOT_DATELIKE = {"a", "A", "m", "M", "p", "P", "t", "T"}
_DEFAULT = _dt.datetime(1, 1, 1)

_ZONE_TAIL = re.compile(r"Z|[+-]\d{2}(?::?\d{2})?")
_YMD_SEPARATORS = "-./\\ "


def _iso(s: str) -> _dt.datetime | None:
    """numpy's ``parse_iso_8601_datetime`` (what pandas' ``string_to_dts`` runs) for zone-free
    text; None if the text is not ISO-8601 by that reader or holds impossible values (pandas then
    falls through to its other readers). Zone suffixes raise ``du.ParsedTimeZone``."""
    t = s.lstrip()
    n = len(t)
    i = 0

    def digit(k: int) -> bool:
        return k < n and t[k] in "0123456789"

    if n < 4 or not all(digit(k) for k in range(4)):
        return None
    year = int(t[:4])
    i = 4
    month = day = 1
    hour = minute = second = micro = 0
    if i == n:
        return _dt.datetime(year, 1, 1)
    sep = ""
    if not digit(i):
        sep = t[i]
        if sep not in _YMD_SEPARATORS:
            return None
        i += 1
        if i == n or not digit(i):
            return None
    if not digit(i):
        return None
    month = int(t[i])
    i += 1
    if digit(i):
        month = month * 10 + int(t[i])
        i += 1
    elif not sep:
        return None
    if not 1 <= month <= 12:
        return None
    if i == n:
        return _dt.datetime(year, month, 1) if sep else None
    if sep:
        if t[i] != sep or i + 1 == n:
            return None
        i += 1
    if not digit(i):
        return None
    day = int(t[i])
    i += 1
    if digit(i):
        day = day * 10 + int(t[i])
        i += 1
    elif not sep:
        return None
    try:
        date = _dt.datetime(year, month, day)
    except ValueError:
        return None
    if i == n:
        return date
    if t[i] not in "T " or i + 1 == n:
        # a run of spaces after the date is allowed trailing whitespace
        return date if not t[i:].strip() else None
    i += 1
    if not digit(i):
        return None
    hour = int(t[i])
    i += 1
    two_digit_hour = digit(i)
    if two_digit_hour:
        hour = hour * 10 + int(t[i])
        i += 1
    elif not sep:
        return None
    if hour > 23:
        return None
    hms_sep = False
    tail_zone = False
    if i == n:
        return None if not two_digit_hour else _with(date, hour)
    if t[i] == ":":
        hms_sep = True
        i += 1
        if i == n or not digit(i):
            return None
    elif not digit(i):
        if not two_digit_hour:
            return None
        tail_zone = True
    if not tail_zone:
        minute = int(t[i])
        i += 1
        if digit(i):
            minute = minute * 10 + int(t[i])
            i += 1
        elif not hms_sep:
            return None
        if minute > 59:
            return None
        if i == n:
            return _with(date, hour, minute)
        if hms_sep:
            if t[i] == ":":
                i += 1
                if i == n or not digit(i):
                    return None
            elif not digit(i):
                tail_zone = True
        elif not digit(i):
            tail_zone = True
        if not tail_zone:
            second = int(t[i])
            i += 1
            if digit(i):
                second = second * 10 + int(t[i])
                i += 1
            elif not hms_sep:
                return None
            if second > 59:
                return None
            if i < n and t[i] in ".,":
                i += 1
                if not digit(i):
                    return None
                start = i
                while digit(i):
                    i += 1
                micro = int(t[start:i][:6].ljust(6, "0"))
    rest = t[i:].strip()
    if rest:
        if _ZONE_TAIL.fullmatch(rest):
            raise du.ParsedTimeZone(s)
        return None
    return _with(date, hour, minute, second, micro)


def _with(
    date: _dt.datetime, hour: int = 0, minute: int = 0, second: int = 0, micro: int = 0
) -> _dt.datetime:
    return date.replace(hour=hour, minute=minute, second=second, microsecond=micro)


def _looks_like_datetime(s: str) -> bool:
    if not s:
        return True
    if s[0] == "0":
        return True
    if s in _NOT_DATELIKE:
        return False
    try:
        return float(s) >= 1000
    except ValueError:
        return True


def _looks_like_time(s: str) -> bool:
    """H:MM or HH:MM at the start of the text."""
    if len(s) < 4:
        return False
    if s[1] == ":":
        hour, minute = s[0], s[2:4]
    elif s[2] == ":":
        hour, minute = s[0:2], s[3:5]
    else:
        return False
    if not (hour.isascii() and hour.isdigit() and minute.isascii() and minute.isdigit()):
        return False
    return 0 <= int(hour) <= 23 and 0 <= int(minute) <= 59 and len(minute) == 2


_DELIMS = "-./"


def _parse_delimited(s: str, dayfirst: bool) -> _dt.datetime | None:
    n = len(s)
    can_swap = False
    month = day = year = -1
    if n == 10 and s[2] in _DELIMS and s[5] in _DELIMS:
        month, day, year, can_swap = _i(s[0:2]), _i(s[3:5]), _i(s[6:10]), True
    elif n == 9 and s[1] in _DELIMS and s[4] in _DELIMS:
        month, day, year, can_swap = _i(s[0:1]), _i(s[2:4]), _i(s[5:9]), True
    elif n == 9 and s[2] in _DELIMS and s[4] in _DELIMS:
        month, day, year, can_swap = _i(s[0:2]), _i(s[3:4]), _i(s[5:9]), True
    elif n == 8 and s[1] in _DELIMS and s[3] in _DELIMS:
        month, day, year, can_swap = _i(s[0:1]), _i(s[2:3]), _i(s[4:8]), True
    elif n == 7 and s[2] in _DELIMS:
        if s[2] == ".":
            return None  # 10.2010 may be a float: pandas refuses to call it a date
        month, year = _i(s[0:2]), _i(s[3:7])
        day = 1
        return _date(year, month, day) if month >= 0 and year >= 1000 else None
    elif n == 6 and s[1] in _DELIMS:
        if s[1] == ".":
            return None
        month, year = _i(s[0:1]), _i(s[2:6])
        day = 1
        return _date(year, month, day) if month >= 0 and year >= 1000 else None
    else:
        return None
    if month < 0 or day < 0 or year < 1000:
        return None
    if 1 <= month <= 31 and 1 <= day <= 31 and (month <= 12 or day <= 12):
        if (month > 12 or (dayfirst and day <= 12)) and can_swap:
            day, month = month, day
        return _date(year, month, day)
    raise ValueError(f"Invalid date specified ({month}/{day})")


def _i(text: str) -> int:
    return int(text) if text.isascii() and text.isdigit() else -1


def _date(year: int, month: int, day: int) -> _dt.datetime:
    return _dt.datetime(year, month, day)


def _parse_dateabbr(s: str) -> _dt.datetime | None:
    """Year-only ("2021"), quarters ("2021Q3", "3Q21") and month-year ("Mar 2021"); None when
    the text is none of these (pandas' ValueError, then on to dateutil)."""
    text = s.upper()
    n = len(text)
    if n == 4:
        try:
            return _DEFAULT.replace(year=int(text))
        except ValueError:
            pass
    try:
        if 4 <= n <= 7:
            i = text.index("Q", 1, 6)
            if i == 1:
                quarter = int(text[0])
                if n == 4 or (n == 5 and text[i + 1] == "-"):
                    year = 2000 + int(text[-2:])
                elif n == 6 or (n == 7 and text[i + 1] == "-"):
                    year = int(text[-4:])
                else:
                    raise ValueError
            elif i in (2, 3):
                quarter = int(text[i + 1 :])
                year = 2000 + int(text[:2])
            elif i in (4, 5):
                quarter = int(text[i + 1 :])
                year = int(text[:4])
            else:
                raise ValueError
            if not 1 <= quarter <= 4:
                raise du.ParserError(
                    f"Incorrect quarterly string is given, quarter must be between 1 and 4: {s}"
                )
            return _DEFAULT.replace(year=year, month=(quarter - 1) * 3 + 1)
    except du.ParserError:
        raise
    except (ValueError, IndexError):
        pass
    for fmt in ("%Y-%m", "%b %Y", "%b-%Y"):
        try:
            return _dt.datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def parse_mixed(s: str, today: _dt.datetime | None = None) -> Any:
    """One string through ``to_datetime(format="mixed")``: a naive ``datetime``, ``_NAT``, or
    ``None`` when pandas raises ValueError."""
    if s == "" or s in NAT_STRINGS:
        return _NAT
    if s.lower() in ("now", "today"):
        return today or _dt.datetime.now()
    try:
        hit = _iso(s)
    except ValueError:
        hit = None  # e.g. year 0: representable for numpy, not for datetime
    except du.ParsedTimeZone as exc:
        raise ZonedTextError(s) from exc
    if hit is not None:
        return hit
    return _parse_datetime_string(s, today)


def _parse_datetime_string(s: str, today: _dt.datetime | None) -> _dt.datetime | None:
    if not _looks_like_datetime(s):
        return None
    try:
        if _looks_like_time(s):
            return _dateutil_parse(s, _midnight(today))
        hit = _parse_delimited(s, False)
        if hit is not None:
            return hit
        hit = _parse_dateabbr(s)
        if hit is not None:
            return hit
        return _dateutil_parse(s, _DEFAULT)
    except du.ParsedTimeZone as exc:
        raise ZonedTextError(s) from exc
    except (ValueError, OverflowError):
        return None


def _midnight(today: _dt.datetime | None) -> _dt.datetime:
    return (today or _dt.datetime.now()).replace(hour=0, minute=0, second=0, microsecond=0)


_UTC_NAMES = {"UTC", "GMT", "Z"}


def _dateutil_parse(s: str, default: _dt.datetime) -> _dt.datetime:
    """pandas' ``dateutil_parse`` (parsing.pyx): dateutil's tokenizer and field resolution, then
    pandas' own assembly, which insists on at least one date/time field."""
    res, _ = du.DEFAULTPARSER._parse(s, dayfirst=False, yearfirst=False)
    if res is None:
        raise ValueError(s)
    repl = {}
    for attr in ("year", "month", "day", "hour", "minute", "second", "microsecond"):
        value = getattr(res, attr)
        if value is not None:
            repl[attr] = value
    if not repl:
        raise ValueError(s)
    if "day" not in repl:
        cyear = default.year if res.year is None else res.year
        cmonth = default.month if res.month is None else res.month
        last = calendar.monthrange(cyear, cmonth)[1]
        if default.day > last:
            repl["day"] = last
    ret = default.replace(**repl)
    if res.weekday is not None and not res.day:
        ret += _dt.timedelta(days=(res.weekday - ret.weekday()) % 7)
    if res.tzoffset is not None or (res.tzname and res.tzname.upper() in _UTC_NAMES):
        raise du.ParsedTimeZone(s)
    if res.tzname:
        raise ValueError(s)
    return ret


# ---------------------------------------------------------------------------------------------
# format guessing and the strict parse that follows it
# ---------------------------------------------------------------------------------------------

_DAY_FORMAT = (("day",), "%d", 2)
_ATTRS_TO_FORMAT: list[tuple[tuple[str, ...], str, int]] = [
    (("year", "month", "day", "hour", "minute", "second"), "%Y%m%d%H%M%S", 0),
    (("year", "month", "day", "hour", "minute"), "%Y%m%d%H%M", 0),
    (("year", "month", "day", "hour"), "%Y%m%d%H", 0),
    (("year", "month", "day"), "%Y%m%d", 0),
    (("hour", "minute", "second"), "%H%M%S", 0),
    (("hour", "minute"), "%H%M", 0),
    (("year",), "%Y", 0),
    (("month",), "%B", 0),
    (("month",), "%b", 0),
    (("month",), "%m", 2),
    _DAY_FORMAT,
    (("hour",), "%H", 2),
    (("minute",), "%M", 2),
    (("second",), "%S", 2),
    (("second", "microsecond"), "%S.%f", 0),
    (("tzinfo",), "%z", 0),
    (("tzinfo",), "%Z", 0),
    (("day_of_week",), "%a", 0),
    (("day_of_week",), "%A", 0),
    (("meridiem",), "%p", 0),
]


def _fill_token(token: str, padding: int) -> str:
    if re.search(r"\d+\.\d+", token) is None:
        return token.zfill(padding)
    seconds, nanoseconds = token.split(".")
    return f"{int(seconds):02d}.{nanoseconds.ljust(9, '0')[:6]}"


def guess_format(s: str, today: _dt.datetime | None = None) -> str | None:
    """``pandas._libs.tslibs.parsing.guess_datetime_format(s, dayfirst=False)``."""
    default = _midnight(today)
    try:
        parsed = _dateutil_parse(s, default)
    except du.ParsedTimeZone as exc:
        raise ZonedTextError(s) from exc
    except (ValueError, OverflowError):
        return None
    tokens: list[str] = du._timelex.split(s)
    format_guess: list[str | None] = [None] * len(tokens)
    found: set[str] = set()
    for attrs, attr_format, padding in _ATTRS_TO_FORMAT:
        if set(attrs) & found:
            continue
        if attr_format in ("%Z", "%z"):
            continue  # zone-free here
        formatted = parsed.strftime(attr_format)
        for i, token_format in enumerate(format_guess):
            filled = _fill_token(tokens[i], padding)
            if token_format is None and filled == formatted:
                format_guess[i] = attr_format
                tokens[i] = filled
                found.update(attrs)
                break
    if (
        len({"year", "month", "day"} & found) != 3
        and format_guess != ["%Y"]
        and not (format_guess == ["%Y", None, "%m"] and tokens[1] == "-")
    ):
        return None
    out: list[str] = []
    for i, guess in enumerate(format_guess):
        if guess is not None:
            out.append(guess)
        else:
            try:
                float(tokens[i])
                return None  # a numeric token that matched nothing: the guess is wrong
            except ValueError:
                pass
            out.append(tokens[i])
    if "%p" in out and "%H" in out:
        out[out.index("%H")] = "%I"
    elif "%p" in out and "%I" in out:
        pass
    fmt = "".join(out)
    try:
        _dt.datetime.strptime(s, fmt)
    except ValueError:
        return None
    if parsed.strftime(fmt) == "".join(tokens):
        return fmt
    return None


def coerce_column(
    values: list[str | None], today: _dt.datetime | None = None, first: str | None = None
) -> list[Any]:
    """``pd.to_datetime(series, errors="coerce")`` over text: the format guessed from the first
    non-null element is applied strictly to every element (failures become NaT); when no
    format can be guessed each element goes through ``parse_mixed``. NaT is ``None``."""
    if first is None:
        first = next(
            (v for v in values if v is not None and v != "" and v not in NAT_STRINGS), None
        )
    fmt = guess_format(first, today) if first is not None else None
    out: list[Any] = []
    for v in values:
        if v is None or v == "" or v in NAT_STRINGS:
            out.append(None)
        elif fmt is not None:
            try:
                out.append(_dt.datetime.strptime(v, fmt))
            except ValueError:
                out.append(None)
        else:
            r = parse_mixed(v, today)
            out.append(None if r is None or r is _NAT else r)
    return out
