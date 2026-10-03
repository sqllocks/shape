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

from shape.drift.engine import DEFAULT_THRESHOLDS as DRIFT_DEFAULTS
from shape.drift.engine import diff_tables, resolve_policy, view_of_profile_column
from shape.profile.reference.profile import Profile

from .joint import check_joint_rules, check_no_placeholder

# --- contract check ---------------------------------------------------------------

_CONTRACT_KEYS = {
    "row_count",
    "columns",
    "required_columns",
    "allow_extra_columns",
    "tables",
    "drift",  # the drift policy (thresholds, ignore, per-column thresholds): ignored by check
    # joint rules (#47), all optional like every rule: absent from a contract, nothing changes
    "fd",  # [{"determinant": "zip", "dependent": "city", "min_confidence": 0.99}]
    "implies",  # [{"if": {"column": "state", "equals": "CA"}, "then": {...}, "min_confidence": 1}]
    "reference_pair",  # [{"columns": ["city", "zip"], "reference": "...", "min_match_rate": 0.99}]
    "max_implausible_rate",  # the share of rows that break a dependency or hold a placeholder
}
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
    "min_true_rate",  # the share of true values of a boolean (or 0/1) column
    "max_true_rate",
    "no_placeholder",  # true, or {"max_share": 0.01, "allow": ["N/A"]} (#47)
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
        for key in ("min_true_rate", "max_true_rate"):
            if key in rules and not (_is_number(rules[key]) and 0 <= rules[key] <= 1):
                raise ContractError(f"{key} for column {name!r} must be a number from 0 to 1")
    required = contract.get("required_columns", [])
    if not isinstance(required, list) or not all(isinstance(c, str) for c in required):
        raise ContractError("'required_columns' must be a list of column names")
    for name, rules in columns.items():
        _validate_no_placeholder(name, rules)
    _validate_joint_rules(contract)


def _validate_no_placeholder(name: str, rules: dict[str, Any]) -> None:
    if "no_placeholder" not in rules:
        return
    rule = rules["no_placeholder"]
    if isinstance(rule, bool):
        return
    if not isinstance(rule, dict) or set(rule) - {"max_share", "allow"}:
        raise ContractError(
            f"no_placeholder for column {name!r} must be true or an object with 'max_share' "
            "and 'allow'"
        )
    if "max_share" in rule and not (_is_number(rule["max_share"]) and 0 <= rule["max_share"] <= 1):
        raise ContractError(f"no_placeholder.max_share for column {name!r} must be from 0 to 1")
    if "allow" in rule and not (
        isinstance(rule["allow"], list) and all(isinstance(v, str) for v in rule["allow"])
    ):
        raise ContractError(f"no_placeholder.allow for column {name!r} must be a list of texts")


def _names(value: Any) -> list[str] | None:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
        return list(value)
    return None


def _validate_reference_pair(contract: dict[str, Any]) -> None:
    if "reference_pair" not in contract:
        return
    rules = contract["reference_pair"]
    if not isinstance(rules, list):
        raise ContractError("'reference_pair' must be a list of rules")
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) - {"columns", "reference", "min_match_rate"}:
            raise ContractError(
                "each 'reference_pair' rule is an object with 'columns', 'reference' and "
                "'min_match_rate'"
            )
        if _names(rule.get("columns")) is None or not isinstance(rule.get("reference"), str):
            raise ContractError(
                "reference_pair: 'columns' is a list of column names, 'reference' the name the "
                "profile gave the reference"
            )
        rate: Any = rule.get("min_match_rate")
        if not (_is_number(rate) and 0 < rate <= 1):
            raise ContractError("reference_pair needs 'min_match_rate' above 0 and up to 1")


def _validate_joint_rules(contract: dict[str, Any]) -> None:
    if "max_implausible_rate" in contract and not (
        _is_number(contract["max_implausible_rate"]) and 0 <= contract["max_implausible_rate"] <= 1
    ):
        raise ContractError("max_implausible_rate must be a number from 0 to 1")
    _validate_reference_pair(contract)
    for key, needs in (("fd", ("determinant", "dependent")), ("implies", ("if", "then"))):
        if key not in contract:
            continue
        rules = contract[key]
        if not isinstance(rules, list):
            raise ContractError(f"'{key}' must be a list of rules")
        for rule in rules:
            if not isinstance(rule, dict) or set(rule) - {*needs, "min_confidence"}:
                raise ContractError(
                    f"each '{key}' rule is an object with {', '.join(repr(n) for n in needs)} "
                    "and 'min_confidence'"
                )
            if any(n not in rule for n in needs):
                raise ContractError(f"a '{key}' rule needs {', '.join(repr(n) for n in needs)}")
            conf: Any = rule.get("min_confidence")
            if not (_is_number(conf) and 0 < conf <= 1):
                raise ContractError(f"a '{key}' rule needs 'min_confidence' above 0 and up to 1")
            if key == "fd":
                if _names(rule["determinant"]) is None or not isinstance(rule["dependent"], str):
                    raise ContractError(
                        "fd: 'determinant' is a column name or a list of them, 'dependent' a name"
                    )
            else:
                for side in ("if", "then"):
                    cond = rule[side]
                    if (
                        not isinstance(cond, dict)
                        or set(cond) != {"column", "equals"}
                        or not isinstance(cond["column"], str)
                    ):
                        raise ContractError(
                            f"implies: '{side}' is an object with 'column' and 'equals'"
                        )


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and not math.isnan(v)


def _plain(tagged: Any) -> Any:
    """``["int", 5]`` -> ``5`` (the profile stores min/max type-tagged). A decimal is stored as
    text (``["Decimal", "1.50"]``); it is compared as the number it is."""
    if not (isinstance(tagged, list) and len(tagged) == 2):
        return None
    if tagged[0] == "Decimal" and isinstance(tagged[1], str):
        try:
            return float(tagged[1])
        except ValueError:
            return tagged[1]
    return tagged[1]


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
    # is_unique / null_rate are None when unknown (no rows were read): no evidence, no violation
    if rules.get("unique") is True and col["is_unique"] is False:
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
    if (
        "max_null_rate" in rules
        and col["null_rate"] is not None
        and col["null_rate"] > rules["max_null_rate"]
    ):
        out.append(_violation(name, "max_null_rate", rules["max_null_rate"], col["null_rate"]))
    if "pattern" in rules and col["pattern"] != rules["pattern"]:
        out.append(_violation(name, "pattern", rules["pattern"], col["pattern"]))
    if "distribution" in rules and col["distribution"] != rules["distribution"]:
        out.append(_violation(name, "distribution", rules["distribution"], col["distribution"]))
    if "allowed_values" in rules:
        out.extend(_check_allowed(name, rules["allowed_values"], col))
    out.extend(_check_true_rate(name, rules, col, row_count))
    if "no_placeholder" in rules:
        out.extend(check_no_placeholder(name, rules["no_placeholder"], col))
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


def _check_true_rate(
    name: str, rules: dict[str, Any], col: dict[str, Any], row_count: int
) -> list[dict[str, Any]]:
    if "min_true_rate" not in rules and "max_true_rate" not in rules:
        return []
    rate = view_of_profile_column(col, row_count).true_rate
    if rate is None:
        return [
            _violation(
                name,
                "true_rate",
                {k: rules[k] for k in ("min_true_rate", "max_true_rate") if k in rules},
                "not a boolean column",
            )
        ]
    out = []
    if "min_true_rate" in rules and rate < rules["min_true_rate"]:
        out.append(_violation(name, "min_true_rate", rules["min_true_rate"], rate))
    if "max_true_rate" in rules and rate > rules["max_true_rate"]:
        out.append(_violation(name, "max_true_rate", rules["max_true_rate"], rate))
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
    violations.extend(check_joint_rules(contract, table))
    if contract.get("allow_extra_columns", True) is False:
        known = set(contract.get("columns", {})) | set(required)
        for name in columns:
            if name not in known:
                violations.append(_violation(name, "extra_column", "absent", "present"))
    return violations


def check(profile: Profile, contract: dict[str, Any] | str | Path) -> CheckResult:
    """Check ``profile`` against a v1 contract (a dict, or the path to a JSON file).

    The contract and the profile must describe the same tables. A ``tables`` contract against a
    single-table profile raises :class:`ContractError`; against a dataset, a table the contract
    names and the profile lacks is a ``table_exists`` violation. Every rule is optional (§12.3), so
    a profile table the contract does not name is not checked.
    """
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
    if "tables" in contract:
        # A multi-table contract has nothing to say about one table: checking it would pass
        # without testing a single rule.
        raise ContractError(
            "the contract has a 'tables' object but the profile is a single table "
            f"({profile.name!r}): profile the tables together as a dataset, one table per file "
            "(`shape profile --dataset FOLDER`, or `shape.profile({name: source, ...})`)"
        )
    violations = _check_table(next(iter(profile.tables.values())), contract)
    return CheckResult(passed=not violations, violations=violations)


# --- drift ------------------------------------------------------------------------
#
# The rules live in ``shape.drift.engine`` (one engine for ``shape.diff``, ``ShapeMonitor`` and
# ``ShapeTimeline.changes``); ``diff`` is its front end for profiles.

DEFAULT_THRESHOLDS: dict[str, Any] = DRIFT_DEFAULTS


@dataclass
class DiffResult:
    """Outcome of :func:`diff`."""

    drifted: bool
    changes: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"drifted": self.drifted, "changes": [dict(c) for c in self.changes]}


def diff(
    baseline: Any,
    current: Any,
    *,
    thresholds: dict[str, Any] | None = None,
    ignore_columns: list[str] | None = None,
    column_thresholds: dict[str, dict[str, Any]] | None = None,
    only_columns: list[str] | None = None,
    policy: dict[str, Any] | str | Path | None = None,
) -> DiffResult:
    """Compare two profiles with the documented defaults (``docs/DRIFT.md``).

    ``baseline`` and ``current`` are profiles; a window of the stream profiler works too.
    ``thresholds`` overrides the defaults for every column, ``column_thresholds`` for the columns
    its patterns match (``{"order_total": {"mean_shift_std": 0.25}, "*": {...}}``),
    ``ignore_columns`` drops columns (a name, ``table.column`` or a glob) and ``only_columns``
    keeps only those. ``policy`` is a dict or JSON file holding the same four settings (a
    contract's ``"drift"`` object works), so a team keeps one policy file.
    """
    resolved = resolve_policy(
        thresholds,
        ignore_columns=ignore_columns,
        column_thresholds=column_thresholds,
        only_columns=only_columns,
        policy=policy,
    )
    changes = diff_tables(baseline, current, resolved)
    return DiffResult(drifted=bool(changes), changes=changes)
