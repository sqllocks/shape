"""Contract format v1 (``shape.check``) and profile drift (``shape.diff``).

Both operate on :class:`shape.profile.reference.Profile` objects. The rule vocabulary
(``dtype``, ``pattern``, ``distribution``) is the profiler's own.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shape.profile.reference.profile import Profile

# --- contract check ---------------------------------------------------------------

_CONTRACT_KEYS = {"row_count", "columns", "required_columns", "allow_extra_columns", "tables"}
_ROW_COUNT_KEYS = {"min", "max"}
_COLUMN_RULES = {
    "dtype",
    "nullable",
    "unique",
    "max_null_rate",
    "pattern",
    "allowed_values",
    "min",
    "max",
    "distribution",
}


class ContractError(ValueError):
    """The contract itself is malformed."""


@dataclass
class CheckResult:
    """Outcome of :func:`check`."""

    passed: bool
    violations: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "violations": [dict(v) for v in self.violations]}


def _violation(column: str | None, rule: str, expected: Any, observed: Any) -> dict[str, Any]:
    return {"column": column, "rule": rule, "expected": expected, "observed": observed}


def _load_contract(contract: dict[str, Any] | str | Path) -> dict[str, Any]:
    if isinstance(contract, (str, Path)):
        try:
            with open(contract, encoding="utf-8") as fh:
                contract = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ContractError(f"contract {contract} is not valid JSON: {exc}") from exc
    if not isinstance(contract, dict):
        raise ContractError("a contract must be a JSON object")
    return contract


def _validate_contract(contract: dict[str, Any]) -> None:
    unknown = set(contract) - _CONTRACT_KEYS
    if unknown:
        raise ContractError(f"unknown contract keys: {sorted(unknown)}")
    rc = contract.get("row_count", {})
    if not isinstance(rc, dict) or set(rc) - _ROW_COUNT_KEYS:
        raise ContractError("row_count accepts only 'min' and 'max'")
    columns = contract.get("columns", {})
    if not isinstance(columns, dict):
        raise ContractError("'columns' must be an object")
    for name, rules in columns.items():
        if not isinstance(rules, dict):
            raise ContractError(f"rules for column {name!r} must be an object")
        bad = set(rules) - _COLUMN_RULES
        if bad:
            raise ContractError(f"unknown rules for column {name!r}: {sorted(bad)}")
        if "allowed_values" in rules and not isinstance(rules["allowed_values"], list):
            raise ContractError(f"allowed_values for column {name!r} must be a list")
    required = contract.get("required_columns", [])
    if not isinstance(required, list) or not all(isinstance(c, str) for c in required):
        raise ContractError("'required_columns' must be a list of column names")


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and not math.isnan(v)


def _plain(tagged: Any) -> Any:
    """``["int", 5]`` -> ``5`` (the profile stores min/max type-tagged)."""
    return tagged[1] if isinstance(tagged, list) and len(tagged) == 2 else None


def _key(value: Any) -> str:
    """The profiler's ``str(key)`` form of a value (``True`` -> ``"True"``)."""
    return str(value)


def _check_column(
    name: str, rules: dict[str, Any], col: dict[str, Any], row_count: int
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if "dtype" in rules and col["dtype"] != rules["dtype"]:
        out.append(_violation(name, "dtype", rules["dtype"], col["dtype"]))
    if rules.get("nullable") is False and col["null_count"] > 0:
        out.append(_violation(name, "nullable", False, {"null_count": col["null_count"]}))
    if rules.get("unique") is True and not col["is_unique"]:
        out.append(
            _violation(
                name,
                "unique",
                True,
                {
                    "cardinality": col["cardinality"],
                    "null_count": col["null_count"],
                    "row_count": row_count,
                },
            )
        )
    if "max_null_rate" in rules and col["null_rate"] > rules["max_null_rate"]:
        out.append(_violation(name, "max_null_rate", rules["max_null_rate"], col["null_rate"]))
    if "pattern" in rules and col["pattern"] != rules["pattern"]:
        out.append(_violation(name, "pattern", rules["pattern"], col["pattern"]))
    if "distribution" in rules and col["distribution"] != rules["distribution"]:
        out.append(_violation(name, "distribution", rules["distribution"], col["distribution"]))
    if "allowed_values" in rules:
        out.extend(_check_allowed(name, rules["allowed_values"], col))
    for bound in ("min", "max"):
        if bound not in rules:
            continue
        observed = _plain(col["min_value" if bound == "min" else "max_value"])
        expected = rules[bound]
        if observed is None:
            continue  # an all-null column has no bound to violate
        comparable = (_is_number(observed) and _is_number(expected)) or (
            isinstance(observed, str) and isinstance(expected, str)
        )
        if not comparable:
            out.append(_violation(name, bound, expected, observed))
            continue
        beyond = observed < expected if bound == "min" else observed > expected
        if beyond:
            out.append(_violation(name, bound, expected, observed))
    return out


def _check_allowed(name: str, allowed: list[Any], col: dict[str, Any]) -> list[dict[str, Any]]:
    allowed_keys = {_key(v) for v in allowed}
    seen = col.get("value_counts_ext") or {}
    outside = [k for k in seen if k not in allowed_keys]
    if outside:
        return [_violation(name, "allowed_values", allowed, {"unexpected_values": outside[:20]})]
    if col["cardinality"] > len(allowed_keys):
        # more distinct values than the set holds: some value must be outside it, even
        # when the profile only stored the most frequent ones
        return [
            _violation(
                name,
                "allowed_values",
                allowed,
                {"cardinality": col["cardinality"], "unexpected_values": []},
            )
        ]
    return []


def _check_table(
    table: dict[str, Any], contract: dict[str, Any], prefix: str = ""
) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    columns = table["columns"]
    rc = contract.get("row_count", {})
    if "min" in rc and table["row_count"] < rc["min"]:
        violations.append(_violation(None, f"{prefix}row_count.min", rc["min"], table["row_count"]))
    if "max" in rc and table["row_count"] > rc["max"]:
        violations.append(_violation(None, f"{prefix}row_count.max", rc["max"], table["row_count"]))
    required = contract.get("required_columns", [])
    for name in required:
        if name not in columns:
            violations.append(_violation(name, "required_column", "present", "missing"))
    for name, rules in contract.get("columns", {}).items():
        if name not in columns:
            if name not in required:
                violations.append(_violation(name, "column_exists", "present", "missing"))
            continue
        violations.extend(_check_column(name, rules, columns[name], table["row_count"]))
    if contract.get("allow_extra_columns", True) is False:
        known = set(contract.get("columns", {})) | set(required)
        for name in columns:
            if name not in known:
                violations.append(_violation(name, "extra_column", "absent", "present"))
    return violations


def check(profile: Profile, contract: dict[str, Any] | str | Path) -> CheckResult:
    """Check ``profile`` against a v1 contract (a dict, or the path to a JSON file)."""
    contract = _load_contract(contract)
    _validate_contract(contract)
    if profile.is_dataset:
        per_table = contract.get("tables")
        if not isinstance(per_table, dict):
            raise ContractError(
                "the profile has several tables: give the contract a 'tables' object "
                "mapping table names to contracts"
            )
        violations: list[dict[str, Any]] = []
        for tname, sub in per_table.items():
            _validate_contract(sub)
            if tname not in profile.tables:
                violations.append(_violation(None, "table_exists", tname, "missing"))
                continue
            for v in _check_table(profile.tables[tname], sub):
                if v["column"] is not None:
                    v["column"] = f"{tname}.{v['column']}"
                else:
                    v["rule"] = f"{tname}:{v['rule']}"
                violations.append(v)
        return CheckResult(passed=not violations, violations=violations)
    violations = _check_table(next(iter(profile.tables.values())), contract)
    return CheckResult(passed=not violations, violations=violations)


# --- drift ------------------------------------------------------------------------

_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}

DEFAULT_THRESHOLDS: dict[str, Any] = {
    "null_rate": 0.05,  # absolute change
    "cardinality_ratio_max": 1.5,
    "cardinality_ratio_min": 0.67,
    "mean_shift_std": 0.5,  # multiples of the baseline standard deviation
    "min_severity": "low",  # changes below this severity are not reported
}


@dataclass
class DiffResult:
    """Outcome of :func:`diff`."""

    drifted: bool
    changes: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"drifted": self.drifted, "changes": [dict(c) for c in self.changes]}


def _change(
    column: str | None, kind: str, baseline: Any, current: Any, severity: str
) -> dict[str, Any]:
    return {
        "column": column,
        "kind": kind,
        "baseline": baseline,
        "current": current,
        "severity": severity,
    }


def _num(v: Any) -> float | None:
    return float(v) if _is_number(v) else None


def _diff_column(
    name: str, base: dict[str, Any], cur: dict[str, Any], th: dict[str, Any]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if base["dtype"] != cur["dtype"]:
        out.append(_change(name, "dtype_change", base["dtype"], cur["dtype"], "high"))
    b_null, c_null = base["null_rate"], cur["null_rate"]
    if abs(c_null - b_null) > th["null_rate"]:
        out.append(_change(name, "null_rate_change", b_null, c_null, "medium"))
    b_card, c_card = base["cardinality"], cur["cardinality"]
    if b_card > 0:
        ratio = c_card / b_card
        if ratio > th["cardinality_ratio_max"] or ratio < th["cardinality_ratio_min"]:
            out.append(_change(name, "cardinality_change", b_card, c_card, "medium"))
    elif c_card > 0:
        out.append(_change(name, "cardinality_change", b_card, c_card, "medium"))
    b_mean, c_mean, b_std = _num(base["mean"]), _num(cur["mean"]), _num(base["std"])
    if b_mean is not None and c_mean is not None:
        limit = th["mean_shift_std"] * (b_std or 0.0)
        if abs(c_mean - b_mean) > limit:
            out.append(_change(name, "mean_shift", b_mean, c_mean, "medium"))
    if base["distribution"] != cur["distribution"]:
        out.append(
            _change(name, "distribution_change", base["distribution"], cur["distribution"], "low")
        )
    b_enum, c_enum = base.get("enum_values"), cur.get("enum_values")
    if b_enum is not None and c_enum is not None:
        new = [v for v in c_enum if v not in b_enum]
        if new:
            out.append(
                _change(name, "new_categorical_values", sorted(b_enum), sorted(c_enum), "low")
            )
    return out


def _diff_table(
    base: dict[str, Any], cur: dict[str, Any], th: dict[str, Any], prefix: str = ""
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    b_cols, c_cols = base["columns"], cur["columns"]
    for name in b_cols:
        if name not in c_cols:
            out.append(_change(prefix + name, "column_removed", "present", "absent", "high"))
    for name in c_cols:
        if name not in b_cols:
            out.append(_change(prefix + name, "column_added", "absent", "present", "high"))
    for name in b_cols:
        if name in c_cols:
            for ch in _diff_column(name, b_cols[name], c_cols[name], th):
                ch["column"] = prefix + name
                out.append(ch)
    return out


def diff(
    baseline: Profile, current: Profile, *, thresholds: dict[str, Any] | None = None
) -> DiffResult:
    """Compare two profiles with the §12.3 defaults (override through ``thresholds``)."""
    th = dict(DEFAULT_THRESHOLDS)
    if thresholds:
        unknown = set(thresholds) - set(DEFAULT_THRESHOLDS)
        if unknown:
            raise ValueError(f"unknown thresholds: {sorted(unknown)}")
        th.update(thresholds)
    if th["min_severity"] not in _SEVERITY_RANK:
        raise ValueError("min_severity must be 'low', 'medium' or 'high'")
    changes: list[dict[str, Any]] = []
    if baseline.is_dataset or current.is_dataset:
        b_tables, c_tables = baseline.tables, current.tables
        for tname in b_tables:
            if tname not in c_tables:
                changes.append(_change(None, "table_removed", tname, None, "high"))
        for tname in c_tables:
            if tname not in b_tables:
                changes.append(_change(None, "table_added", None, tname, "high"))
        for tname in b_tables:
            if tname in c_tables:
                changes.extend(_diff_table(b_tables[tname], c_tables[tname], th, f"{tname}."))
    else:
        changes = _diff_table(
            next(iter(baseline.tables.values())), next(iter(current.tables.values())), th
        )
    floor = _SEVERITY_RANK[th["min_severity"]]
    changes = [c for c in changes if _SEVERITY_RANK[c["severity"]] >= floor]
    return DiffResult(drifted=bool(changes), changes=changes)
