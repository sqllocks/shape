"""One rule-by-rule evaluation of a contract, shared by mutation testing and backtesting.

``shape.check`` answers pass or fail for a whole contract. A rule test needs more: for every rule
the contract declares, did it pass, fail, or could the profile not say (``not_measured``)? A rule
that could not be measured is counted apart and is never a pass.

A rule is identified as ``table.column.rule`` (``table.rule`` when it has no column, for example
``orders.row_count.min``). The evaluation reuses the checks of :mod:`shape.contracts.v1`; it only
adds the question of whether the profile holds the statistic the rule reads.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from shape.contracts.v1 import ContractError, _check_table, _load_contract, _validate_contract

PASS = "pass"
FAIL = "fail"
NOT_MEASURED = "not_measured"

# What a rule reads from the column of a profile. ``True``: the profile must hold the field;
# a merged profile leaves the "whole data" fields unknown.
_WHOLE_DATA_RULES = frozenset(
    {
        "pattern",
        "distribution",
        "allowed_values",
        "min_true_rate",
        "max_true_rate",
        "no_placeholder",
    }
)
_NEEDS_FIELD: dict[str, tuple[str, ...]] = {
    "dtype": ("dtype",),
    "nullable": ("null_count",),
    "unique": ("is_unique",),
    "max_null_rate": ("null_rate",),
    "pattern": ("pattern",),
    "distribution": ("distribution",),
    "allowed_values": ("value_counts_ext", "cardinality"),
    "min": ("min_value",),
    "max": ("max_value",),
    "min_true_rate": ("value_counts_ext",),
    "max_true_rate": ("value_counts_ext",),
}
_MAY_BE_NONE = frozenset({"pattern", "distribution", "value_counts_ext"})
# Defaults so the v1 column check can run on a stored form that lacks a statistic; the outcome of
# a rule that reads a missing statistic is replaced by ``not_measured``.
_FILL: dict[str, Any] = {
    "dtype": None,
    "null_count": 0,
    "null_rate": None,
    "cardinality": 0,
    "is_unique": None,
    "pattern": None,
    "distribution": None,
    "min_value": None,
    "max_value": None,
    "value_counts_ext": None,
}


@dataclass(frozen=True)
class RuleOutcome:
    """The outcome of one declared rule on one profile."""

    id: str
    status: str
    observed: Any = None


def rule_id(table: str, column: str | None, rule: str) -> str:
    return f"{table}.{column}.{rule}" if column else f"{table}.{rule}"


def _label_fd(rule: Mapping[str, Any]) -> str:
    det = rule["determinant"]
    det = [det] if isinstance(det, str) else list(det)
    return f"{', '.join(det)} -> {rule['dependent']}"


def _label_implies(rule: Mapping[str, Any]) -> str:
    a, b = rule["if"], rule["then"]
    return f"{a['column']}={a['equals']!r} => {b['column']}={b['equals']!r}"


def _label_pair(rule: Mapping[str, Any]) -> str:
    cols = rule["columns"]
    cols = [cols] if isinstance(cols, str) else list(cols)
    return f"{', '.join(cols)} in {rule['reference']}"


def declared(table: str, contract: Mapping[str, Any]) -> list[tuple[str, str | None, str]]:
    """The rules a (sub)contract declares, as ``(id, column, rule)`` in a stable order."""
    out: list[tuple[str, str | None, str]] = []
    rc = contract.get("row_count", {})
    for key in ("min", "max"):
        if key in rc:
            out.append((rule_id(table, None, f"row_count.{key}"), None, f"row_count.{key}"))
    for name in contract.get("required_columns", []):
        out.append((rule_id(table, name, "required_column"), name, "required_column"))
    for name, rules in contract.get("columns", {}).items():
        for rule in rules:
            out.append((rule_id(table, name, rule), name, rule))
    if contract.get("allow_extra_columns", True) is False:
        out.append((rule_id(table, None, "extra_column"), None, "extra_column"))
    for rule in contract.get("fd", ()):
        label = _label_fd(rule)
        out.append((rule_id(table, label, "fd"), label, "fd"))
    for rule in contract.get("implies", ()):
        label = _label_implies(rule)
        out.append((rule_id(table, label, "implies"), label, "implies"))
    for rule in contract.get("reference_pair", ()):
        label = _label_pair(rule)
        out.append((rule_id(table, label, "reference_pair"), label, "reference_pair"))
    if "max_implausible_rate" in contract:
        out.append((rule_id(table, None, "max_implausible_rate"), None, "max_implausible_rate"))
    return out


def _measured(rule: str, col: Mapping[str, Any], merged: bool, full: bool) -> bool:
    """True when the column holds what ``rule`` reads (and a merge did not leave it unknown).

    ``full`` is False for a stored form that keeps only some statistics (the share-safe profile):
    a column without placeholders lists none, so only a full profile can show their absence."""
    if merged and rule in _WHOLE_DATA_RULES:
        return False
    if rule == "no_placeholder" and not full:
        return False
    for field in _NEEDS_FIELD.get(rule, ()):
        if field not in col:
            return False
        if col[field] is None:
            if field in ("min_value", "max_value"):  # exact, also in a merge: None is "all null"
                continue
            if field in _MAY_BE_NONE and full and not merged:
                continue  # a full profile stores None for "none detected"
            return False
    return True


def _violation_id(table: str, v: Mapping[str, Any]) -> list[str]:
    rule = str(v["rule"])
    column = v["column"]
    if rule == "extra_column":
        return [rule_id(table, None, rule)]
    if rule == "true_rate":
        return [rule_id(table, column, k) for k in ("min_true_rate", "max_true_rate")]
    return [rule_id(table, column, rule)]


def _is_not_measured(v: Mapping[str, Any]) -> bool:
    return isinstance(v["observed"], str) and v["observed"].startswith("not measured")


def evaluate_table(
    table: str,
    profile: Mapping[str, Any],
    contract: Mapping[str, Any],
    *,
    merged: bool = False,
    full: bool = True,
) -> list[RuleOutcome]:
    """Every declared rule of ``contract`` on one table profile, plus any other rule that fired
    (a missing column, for example)."""
    columns = dict(profile["columns"])
    patched = {
        "row_count": int(profile.get("row_count", 0)),
        "columns": {n: {**_FILL, **c} for n, c in columns.items()},
        "joint": profile.get("joint"),
    }
    violations = _check_table(patched, dict(contract))
    wanted = declared(table, contract)
    unmeasured: set[str] = set()
    for rid, column, rule in wanted:
        if column in columns and not _measured(rule, columns[column], merged, full):
            unmeasured.add(rid)
        if merged and rule in ("fd", "implies", "reference_pair", "max_implausible_rate"):
            unmeasured.add(rid)
    failed: dict[str, Any] = {}
    for v in violations:
        ids = _violation_id(table, v)
        if _is_not_measured(v):
            unmeasured.update(ids)
            continue
        for rid in ids:
            failed.setdefault(rid, v["observed"])
    out: list[RuleOutcome] = []
    seen: set[str] = set()
    for rid, _column, _rule in wanted:
        if rid in seen:
            continue
        seen.add(rid)
        if rid in unmeasured:
            out.append(RuleOutcome(rid, NOT_MEASURED))
        elif rid in failed:
            out.append(RuleOutcome(rid, FAIL, failed[rid]))
        else:
            out.append(RuleOutcome(rid, PASS))
    for rid, observed in failed.items():
        if rid not in seen and rid not in unmeasured:
            seen.add(rid)
            out.append(RuleOutcome(rid, FAIL, observed))
    return out


def evaluate(
    tables: Mapping[str, Mapping[str, Any]],
    is_dataset: bool,
    contract: dict[str, Any] | str | Any,
    *,
    merged: bool = False,
    full: bool = True,
) -> list[RuleOutcome]:
    """Evaluate ``contract`` on the table profiles of one stored profile.

    ``tables`` maps a table name to its table-profile dictionary. A dataset needs a contract with
    a ``tables`` object (as ``shape.check`` does); a single table needs one without.
    """
    doc = _load_contract(contract)
    _validate_contract(doc)
    if not is_dataset:
        if "tables" in doc:
            raise ContractError(
                "the contract has a 'tables' object but the profile is a single table: profile "
                "the tables together as a dataset"
            )
        ((name, prof),) = tables.items()
        return evaluate_table(name, prof, doc, merged=merged, full=full)
    per_table = doc.get("tables")
    if not isinstance(per_table, dict):
        raise ContractError(
            "the profile has several tables: give the contract a 'tables' object mapping table "
            "names to contracts"
        )
    out: list[RuleOutcome] = []
    for tname, sub in per_table.items():
        _validate_contract(sub)
        if tname not in tables:
            out.append(RuleOutcome(rule_id(tname, None, "table_exists"), FAIL, "missing"))
            continue
        out.extend(evaluate_table(tname, tables[tname], sub, merged=merged, full=full))
    return out


def contract_rule_ids(contract: dict[str, Any] | str | Any, tables: list[str]) -> list[str]:
    """Every rule a contract declares, without a profile (the rules of the mutation report)."""
    doc = _load_contract(contract)
    _validate_contract(doc)
    if "tables" in doc:
        ids = []
        for tname, sub in doc["tables"].items():
            _validate_contract(sub)
            ids += [r[0] for r in declared(tname, sub)]
    else:
        name = tables[0] if tables else "table"
        ids = [r[0] for r in declared(name, doc)]
    return list(dict.fromkeys(ids))
