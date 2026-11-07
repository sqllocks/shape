"""Whole-value, linear-time RE2 column rules; results contain aggregates only."""

from __future__ import annotations

from typing import Any

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
        isinstance(value, dict) and set(value) - {"expression", "min_match_rate"}
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
    from shape.profile.reference.column import _keys_py, _object_text
    from shape.profile.reference.readers import _arrow_cols

    from .v1 import ContractError

    try:
        col = _arrow_cols(table.select([name]))[0]
        if col.kind in ("nested", "objmix") or col.tz:
            raise ValueError("ambiguous text")
        values = pc.drop_null(col.arr).combine_chunks()
        if col.kind == "float" and not pc.all(pc.is_finite(values)).as_py() and len(values):
            raise ValueError("the profiler omits nonfinite values")
        if col.kind.startswith("obj") or col.kind == "cat":
            return pa.array([_object_text(col.kind, v) for v in values.to_pylist()], pa.string())
        return pa.array(_keys_py(values, col.kind), pa.string())
    except (ValueError, TypeError, pa.ArrowException):
        raise ContractError(
            f"regex for column {name!r}: cannot unambiguously convert to text"
        ) from None


def regex_violations(contract: dict[str, Any], tables: dict[str, Any]) -> list[dict[str, Any]]:
    from .v1 import ContractError, _column_strength, _strong, _violation

    result = []
    per_table = contract.get("tables")
    if per_table is None:
        if not uses_regex(contract):
            return []
        if len(tables) != 1:
            raise ContractError("regex: a single-table contract needs exactly one data table")
        per_table = {next(iter(tables)): contract}
    for table_name, sub in per_table.items():
        for name, rules in sub.get("columns", {}).items():
            if "regex" not in rules:
                continue
            if table_name not in tables or name not in tables[table_name].column_names:
                raise ContractError(f"regex for column {table_name}.{name}: data column is missing")
            expression, rate = regex_options(rules["regex"])
            texts = _texts(tables[table_name], name)
            tested = len(texts)
            matching = (
                pc.sum(
                    pc.cast(pc.match_substring_regex(texts, anchored(expression)), pa.int64())
                ).as_py()
                or 0
            )
            match_rate = matching / tested if tested else 1.0
            if match_rate < rate:
                column = f"{table_name}.{name}" if "tables" in contract else name
                result.append(
                    _strong(
                        _violation(
                            column,
                            "regex",
                            {"expression": expression, "min_match_rate": rate},
                            {
                                "match_rate": match_rate,
                                "non_matching_count": tested - matching,
                                "tested_count": tested,
                            },
                        ),
                        _column_strength(rules, "regex"),
                    )
                )
    return result
