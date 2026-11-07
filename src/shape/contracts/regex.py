"""Whole-value, linear-time RE2 column rules; privacy-controlled failure samples."""

from __future__ import annotations

import datetime as dt
from typing import Any, cast

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]


def regex_options(value: Any) -> tuple[str, float]:
    """Return the expression and defaulted match rate of a validated rule."""
    if isinstance(value, str):
        return value, 1.0
    return value["expression"], value.get("min_match_rate", 1.0)


def anchored(expression: str) -> str:
    return f"^(?:{expression})$"


def validate_regex(name: str, value: Any) -> None:
    from .v1 import ContractRuleError, _is_finite_number

    where = f"regex for column {name!r}"
    if not isinstance(value, (str, dict)) or (
        isinstance(value, dict) and set(value) - {"expression", "min_match_rate", "strength"}
    ):
        raise ContractRuleError(
            f"{where} must be text or an object with expression and min_match_rate"
        )
    expression = value if isinstance(value, str) else value.get("expression")
    if not isinstance(expression, str) or not expression:
        raise ContractRuleError(f"{where}: expression must be non-empty text")
    rate = 1.0 if isinstance(value, str) else value.get("min_match_rate", 1.0)
    if not (_is_finite_number(rate) and 0 <= rate <= 1):
        raise ContractRuleError(f"{where}: min_match_rate must be a finite number from 0 to 1")
    if isinstance(value, dict) and "strength" in value:
        from .v1 import _check_strength

        _check_strength(value["strength"], where)
    try:
        pc.match_substring_regex(pa.array([""], type=pa.string()), anchored(expression))
    except pa.ArrowInvalid:
        raise ContractRuleError(f"{where}: expression is not valid RE2 syntax") from None


def uses_regex(contract: dict[str, Any]) -> bool:
    return any("regex" in r for r in contract.get("columns", {}).values()) or any(
        uses_regex(sub) for sub in contract.get("tables", {}).values()
    )


def _texts(table: Any, name: str) -> Any:
    """Use the profiler's Arrow reader and text spelling; refuse nested values."""
    from shape.profile.reference.column import (
        _aware_datetimes,
        _keys_py,
        _nanoseconds,
        _object_text,
        _to_timestamp,
    )
    from shape.profile.reference.readers import _arrow_cols

    from .v1 import ContractError

    try:
        col = _arrow_cols(table.select([name]))[0]
        if col.kind in ("nested", "objmix"):
            raise ValueError("ambiguous text")
        values = pc.drop_null(col.arr).combine_chunks()
        if col.kind == "float" and not pc.all(pc.is_finite(values)).as_py() and len(values):
            raise ValueError("the profiler omits nonfinite values")
        if col.kind == "dt64" and col.tz:
            aware = _aware_datetimes(values, col.tz)
            return pa.array(
                [
                    str(_to_timestamp(cast(dt.datetime, v), ns))
                    for v, ns in zip(aware, _nanoseconds(values), strict=True)
                ],
                pa.string(),
            )
        if col.kind.startswith("obj") or col.kind == "cat":
            return pa.array([_object_text(col.kind, v) for v in values.to_pylist()], pa.string())
        return pa.array(_keys_py(values, col.kind), pa.string())
    except (ValueError, TypeError, pa.ArrowException):
        raise ContractError(
            f"regex for column {name!r}: cannot unambiguously convert to text"
        ) from None


def observed_matches(
    texts: Any, counts: list[int], expression: str, reveal: bool
) -> dict[str, Any]:
    matched = pc.match_substring_regex(texts, anchored(expression)).to_pylist()
    tested = sum(counts)
    failing = sum(n for n, ok in zip(counts, matched, strict=True) if not ok)
    samples = (
        list(dict.fromkeys(v for v, ok in zip(texts.to_pylist(), matched, strict=True) if not ok))[
            :20
        ]
        if reveal
        else []
    )
    return {
        "match_rate": (tested - failing) / tested if tested else 1.0,
        "non_matching_count": failing,
        "tested_count": tested,
        "non_matching_values": samples,
    }


def profile_regex(
    name: str, rule: Any, col: dict[str, Any], rows: int, reveal: bool
) -> tuple[dict[str, Any] | None, bool]:
    """Return a definite violation, or an exact pass; incomplete evidence stays blind."""
    import math

    from shape.privacy.redact import has_folded_categories

    from .v1 import _violation

    expression, rate = regex_options(rule)
    seen = col.get("value_counts_ext") or {}
    non_null = rows - col.get("null_count", 0)
    if (
        has_folded_categories(col)
        or "__OTHER__" in seen
        or any(len(k) > 256 and k.endswith("…") for k in seen)
    ):
        return None, True
    values = list(seen)
    weights = list(seen.values())
    # Profiles store six-place proportions of non-null values, rather than raw counts.
    proportions = bool(weights) and all(0 <= w <= 1 for w in weights) and sum(weights) <= 1.00001
    counts = [round(w * non_null) for w in weights] if proportions else [int(w) for w in weights]
    unique_counts = (
        not proportions
        or len(values) <= 1
        or all(
            math.ceil((weight - 0.0000005) * non_null - 1e-9)
            == math.floor((weight + 0.0000005) * non_null + 1e-9)
            for weight in weights
        )
    )
    complete = (
        len(values) == col.get("cardinality")
        and sum(counts) == non_null
        and unique_counts
        and not col.get("nan_count")
        and not col.get("inf_count")
    )
    observed = observed_matches(pa.array(values, pa.string()), counts, expression, reveal)
    definite = rate == 1 and observed["non_matching_count"] > 0
    if complete or definite:
        if observed["match_rate"] < rate:
            return _violation(
                name, "regex", {"expression": expression, "min_match_rate": rate}, observed
            ), False
        return None, False
    return None, True
