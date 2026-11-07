"""Profile and exact-data paths for value rules, sharing capture privacy decisions."""

from __future__ import annotations

import datetime as dt
from typing import Any

from shape.privacy.redact import is_captured_safe, redact_profile

from .regex import _texts, observed_matches, profile_regex, regex_options
from .relative import is_relative, relative_violation, temporal, validate_timezone


def check_value_rules(
    profile: Any, contract: dict[str, Any], data: Any, day: dt.date | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from .v1 import ContractError, _column_strength, _gap, _plain, _strong, _violation

    violations: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    from .regex import uses_regex

    safe_tables = redact_profile(profile).tables if uses_regex(contract) else profile.tables
    per_table = contract.get("tables")
    if per_table is None:
        per_table = {next(iter(profile.tables)): contract}
    for tname, sub in per_table.items():
        if tname not in profile.tables:
            continue
        table = profile.tables[tname]
        columns = table["columns"]
        zone = validate_timezone(sub.get("as_of_timezone", contract.get("as_of_timezone", "UTC")))
        for name, rules in sub.get("columns", {}).items():
            if name not in columns:
                continue
            col = columns[name]
            full_name = f"{tname}.{name}" if profile.is_dataset else name
            has_data_rule = "regex" in rules or any(
                is_relative(rules.get(k)) for k in ("min", "max")
            )
            source = None
            if data is not None and has_data_rule:
                source = (
                    data.get(tname)
                    if profile.is_dataset
                    else next(iter(data.values()), None)
                    if len(data) == 1
                    else None
                )
                if source is None or name not in source.column_names:
                    raise ContractError(
                        f"value rules for column {full_name!r}: data column is missing"
                    )
            observed: Any
            if "regex" in rules:
                reveal = not is_captured_safe(col) and not is_captured_safe(
                    safe_tables[tname]["columns"][name]
                )
                if source is not None:
                    expression, rate = regex_options(rules["regex"])
                    texts = _texts(source, name)
                    if reveal:
                        reveal = _exact_samples_allowed(
                            texts, safe_tables[tname]["columns"][name], profile
                        )
                    observed = observed_matches(texts, [1] * len(texts), expression, reveal)
                    found = (
                        _violation(
                            full_name,
                            "regex",
                            {"expression": expression, "min_match_rate": rate},
                            observed,
                        )
                        if observed["match_rate"] < rate
                        else None
                    )
                    blind = False
                else:
                    rows = col.get("row_count", table["row_count"])
                    found, blind = profile_regex(full_name, rules["regex"], col, rows, reveal)
                if found:
                    violations.append(_strong(found, _column_strength(rules, "regex")))
                if blind:
                    gap = _gap(full_name, "regex")
                    gap["reason"] = (
                        f"regex for {full_name} has incomplete or withheld stored values; "
                        'pass --data or re-profile with capture="full"'
                    )
                    gaps.append(gap)
            for bound in ("min", "max"):
                rule = rules.get(bound)
                if not is_relative(rule):
                    continue
                assert day is not None
                if bound + "_value" in col.get("redacted", {}):
                    gaps.append(_gap(full_name, bound))
                    continue
                observed = _plain(col.get(bound + "_value"))
                if source is not None:
                    values: list[Any] = [v for v in source[name].to_pylist() if v is not None]
                    try:
                        pick = min if bound == "min" else max
                        observed = pick(values, key=lambda v: temporal(v, zone)) if values else None
                    except (ValueError, TypeError, OverflowError):
                        observed = next(
                            (v for v in values if not _parseable(v, zone)),
                            {"not_comparable": "mixed date and timestamp values"},
                        )
                    if isinstance(observed, (dt.date, dt.datetime)):
                        observed = observed.isoformat()
                found = relative_violation(full_name, bound, rule, observed, day, zone)
                if found:
                    violations.append(_strong(found, _column_strength(rules, bound)))
    return violations, gaps


def _parseable(value: Any, zone: Any) -> bool:
    try:
        temporal(value, zone)
        return True
    except (ValueError, TypeError, OverflowError):
        return False


def _exact_samples_allowed(texts: Any, safe_column: dict[str, Any], profile: Any) -> bool:
    """New or newly rare data values must not bypass the profile's release policy."""
    from collections import Counter

    from shape.privacy.safe_profile import K_DEFAULT

    released = safe_column.get("value_counts_ext") or {}
    counts = Counter(texts.to_pylist())
    k = profile.capture.get("k") or K_DEFAULT
    return all(value in released and count >= k for value, count in counts.items())
