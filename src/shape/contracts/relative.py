"""Explicit as-of dates and calendar-day bounds without losing timezone information."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def validate_timezone(value: Any) -> ZoneInfo:
    from .v1 import ContractRuleError

    try:
        if not isinstance(value, str):
            raise ValueError
        return ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError):
        raise ContractRuleError("as_of_timezone must be a valid IANA timezone name") from None


def validate_bound(name: str, bound: str, value: dict[str, Any]) -> None:
    from .v1 import ContractRuleError

    offset = value.get("as_of_offset_days")
    if set(value) != {"as_of_offset_days"} or type(offset) is not int:
        raise ContractRuleError(
            f"{bound} for column {name!r} requires only integer as_of_offset_days"
        )


def is_relative(value: Any) -> bool:
    return isinstance(value, dict) and "as_of_offset_days" in value


def has_relative(contract: dict[str, Any]) -> bool:
    return any(
        is_relative(r.get(k)) for r in contract.get("columns", {}).values() for k in ("min", "max")
    ) or any(has_relative(sub) for sub in contract.get("tables", {}).values())


def parse_as_of(value: Any, contract: dict[str, Any]) -> dt.date | None:
    from .v1 import ContractError

    if value is None:
        if has_relative(contract):
            raise ContractError("relative min/max bounds require as_of=: pass --as-of YYYY-MM-DD")
        return None
    if type(value) is dt.date:
        return value
    try:
        if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return dt.date.fromisoformat(value)
    except ValueError:
        pass
    raise ContractError("as_of must be a date or YYYY-MM-DD: pass --as-of YYYY-MM-DD")


def temporal(value: Any, zone: ZoneInfo) -> dt.date | dt.datetime:
    """Read an ISO temporal value; a date remains a date, an instant remains an instant."""
    if isinstance(value, dt.datetime):
        result = value
    elif isinstance(value, dt.date):
        return value
    elif isinstance(value, str):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return dt.date.fromisoformat(value)
        if not re.match(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", value):
            raise ValueError("not an ISO timestamp")
        result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("not a date or timestamp")
    if result.tzinfo is None:
        result = result.replace(tzinfo=zone)
    return result.astimezone(dt.UTC)


def relative_violation(
    name: str, bound: str, rule: dict[str, Any], observed: Any, day: dt.date, zone: ZoneInfo
) -> dict[str, Any] | None:
    from .v1 import ContractError, _violation

    try:
        resolved = day + dt.timedelta(days=rule["as_of_offset_days"])
        next_day = resolved + dt.timedelta(days=1)
    except (OverflowError, ValueError):
        raise ContractError(
            f"{bound} for column {name!r}: relative day is outside the date range"
        ) from None
    expected: dict[str, Any] = {**rule, "as_of": day.isoformat(), "bound": resolved.isoformat()}
    if observed is None:
        return None
    try:
        actual = temporal(observed, zone)
        if isinstance(actual, dt.datetime):
            edge = dt.datetime.combine(resolved if bound == "min" else next_day, dt.time(), zone)
            expected["bound"] = edge.isoformat()
            beyond = (
                actual < edge.astimezone(dt.UTC)
                if bound == "min"
                else actual >= edge.astimezone(dt.UTC)
            )
        else:
            beyond = actual < resolved if bound == "min" else actual > resolved
    except (ValueError, TypeError, OverflowError):
        return _violation(name, bound, expected, observed)
    return _violation(name, bound, expected, observed) if beyond else None
