"""What the four emitters share: the contract's normal form, the pattern table, the list of what
a target leaves out, and the target-independent reasons.

A contract ``pattern`` is a label (``email``, ``uuid``...) that the profiler gives a column
when at least 90% of a sample matches it. :data:`PATTERNS` is the regular expression behind each
label; the emitted checks apply it to every row (Great Expectations, which has ``mostly``, keeps
the 90%).
"""

from __future__ import annotations

import copy
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

FORMAT = "shape-contract-emit"
VERSION = 1

DTYPES = ("boolean", "integer", "float", "string", "date", "datetime")
JOINT_RULES = ("fd", "implies", "reference_pair", "max_implausible_rate")
NULL_RATE_PLACES = 9
DEFAULT_TABLE = "table"

_OCT = r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)"
_IPV4 = rf"^{_OCT}\.{_OCT}\.{_OCT}\.{_OCT}$"
_IPV6 = (
    r"^(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}$"
    r"|^(?:[0-9a-fA-F]{1,4}:){1,7}:$"
    r"|^:(?::[0-9a-fA-F]{1,4}){1,7}$"
    r"|^(?:[0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}$"
    r"|^::(?:[fF]{4}:){0,1}\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$"
    r"|^::$"
)

#: The regular expression of each pattern label the profiler detects (``shape.profile``).
PATTERNS: dict[str, str] = {
    "email": r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$",
    "uuid": r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
    "ssn": r"^\d{3}-\d{2}-\d{4}$",
    "mac_address": r"^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$|^([0-9a-fA-F]{2}-){5}[0-9a-fA-F]{2}$",
    "ip_address": f"(?:{_IPV4})|(?:{_IPV6})",
    "iban": r"^[A-Z]{2}\d{2}[A-Z0-9]{1,30}$",
    "postal_code": r"^\d{5}(-\d{4})?$",
    "date": r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$",
    "phone": r"^[\+]?[\d\s\-\(\)\.]{7,20}$",
    "currency_code": r"^[A-Z]{3}$",
    "language_code": r"^[a-z]{2}(-[A-Z]{2})?$",
}
LABELS = {regex: label for label, regex in PATTERNS.items()}

#: Reasons that do not depend on the target.
JOINT_REASON = "a joint rule relates several columns or rows; the target has no such construct"
DISTRIBUTION_REASON = "a distribution is a property of many rows, not a rule one row can break"
RATE_REASON = "a rate is a property of many rows, not a rule one row can break"
PLACEHOLDER_REASON = "a placeholder rule needs a list and a share of the rows, not a per-row test"


def is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


@dataclass
class Notes:
    """The rules a target cannot state, as ``{"table", "column", "rule", "reason"}``."""

    items: list[dict[str, Any]] = field(default_factory=list)

    def add(self, table: str, column: str | None, rule: str, reason: str) -> None:
        self.items.append({"table": table, "column": column, "rule": rule, "reason": reason})

    def result(self) -> list[dict[str, Any]]:
        return sorted(self.items, key=lambda i: (i["table"], i["column"] or "", i["rule"]))


Why = Callable[[str, Any], "str | None"]


def split_rules(rules: Mapping[str, Any], why: Why) -> tuple[dict[str, Any], dict[str, str]]:
    """``(expressed, {rule: reason})``: ``why(rule, value)`` is a reason, or ``None`` when the
    target can state the rule."""
    ok: dict[str, Any] = {}
    no: dict[str, str] = {}
    for rule, value in rules.items():
        reason = why(rule, value)
        if reason is None:
            ok[rule] = value
        else:
            no[rule] = reason
    return ok, no


def why_common(rule: str, value: Any) -> str | None:
    """The reason a value cannot be stated by any target (``None``: it can)."""
    if rule == "dtype" and value not in DTYPES:
        return f"unknown dtype {value!r}; the dtypes are {', '.join(DTYPES)}"
    if rule in ("min", "max") and not is_number(value):
        return "a bound that is not a number has no numeric check"
    if rule == "allowed_values" and not value:
        return "an empty set of allowed values rejects every value"
    if rule == "pattern" and value not in PATTERNS:
        return f"no regular expression is known for the pattern label {value!r}"
    if rule == "distribution":
        return DISTRIBUTION_REASON
    if rule in ("min_true_rate", "max_true_rate"):
        return RATE_REASON
    if rule == "no_placeholder":
        return PLACEHOLDER_REASON
    return None


def why_row_count(value: Any) -> str | None:
    if not isinstance(value, Mapping) or not all(is_number(v) for v in value.values()):
        return "the row count bounds are not numbers"
    return None


# ---- contract -> normal form ----------------------------------------------------------------


def normalize_table(contract: Mapping[str, Any]) -> dict[str, Any]:
    """One table's contract without the rules that say nothing (``nullable: true``,
    ``unique: false``, ``allow_extra_columns: true``), a column with no other rules listed in
    ``required_columns`` (``shape check`` requires every column a contract names), the null rate
    rounded to nine places, ``required_columns`` sorted and without repeats, and the ``None``
    of an ``allowed_values`` list removed (a null is ``nullable``'s business). Two contracts that
    mean the same compare equal."""
    out: dict[str, Any] = {}
    rc = {k: v for k, v in (contract.get("row_count") or {}).items() if k in ("min", "max")}
    if rc:
        out["row_count"] = rc
    columns: dict[str, Any] = {}
    bare: list[str] = []
    for name, rules in (contract.get("columns") or {}).items():
        kept: dict[str, Any] = {}
        for key, value in rules.items():
            if key == "nullable" and value is not False:
                continue
            if key == "unique" and value is not True:
                continue
            if key == "no_placeholder" and value is False:
                continue
            if key == "max_null_rate" and is_number(value):
                value = round(float(value), NULL_RATE_PLACES)
            if key == "allowed_values" and isinstance(value, list):
                value = [v for v in value if v is not None]
            kept[key] = copy.deepcopy(value)
        if kept:
            columns[name] = kept
        else:
            bare.append(name)  # no rule but its presence: a required column (#641)
    if columns:
        out["columns"] = columns
    required = [*(contract.get("required_columns") or ()), *bare]
    if required:
        out["required_columns"] = sorted(set(required))
    if contract.get("allow_extra_columns") is False:
        out["allow_extra_columns"] = False
    for key in JOINT_RULES:
        if key in contract:
            out[key] = copy.deepcopy(contract[key])
    return out


def prepare(contract: Any, table: str | None) -> dict[str, dict[str, Any]]:
    """Validate ``contract`` (a dict or a path) and return ``{table name: normalized table
    contract}`` for the tables to emit: all of them, or the one ``table`` names. A single-table
    contract is one table, called ``table`` (or the name given)."""
    from shape.contracts.v1 import ContractError, _load_contract, _validate_contract

    from . import EmitError

    doc = _load_contract(contract)
    _validate_contract(doc)
    if "tables" not in doc:
        _check_numbers(doc, None)
        return {table or DEFAULT_TABLE: normalize_table(doc)}
    subs = doc["tables"]
    if not isinstance(subs, dict):
        raise ContractError("'tables' must be an object mapping table names to contracts")
    for name, sub in subs.items():
        if not isinstance(sub, dict):
            raise ContractError(f"table {name!r} of 'tables' must be a contract object")
        if "tables" in sub:
            raise ContractError("'tables' cannot be nested")
        _validate_contract(sub)
        _check_numbers(sub, name)
    if table is not None:
        if table not in subs:
            raise EmitError(
                f"unknown table {table!r}; the contract has {', '.join(sorted(subs)) or 'none'}"
            )
        return {table: normalize_table(subs[table])}
    return {name: normalize_table(sub) for name, sub in subs.items()}


_RATES = ("max_null_rate", "min_true_rate", "max_true_rate")


def _check_numbers(contract: Mapping[str, Any], table: str | None) -> None:
    """Refuse a NaN or infinite number, or a rate outside 0..1, in a contract to emit: no
    target can state it, and JSON (JSON Schema, GX) cannot even hold it (#644)."""
    from shape.contracts.v1 import ContractError

    where = f"table {table!r} " if table is not None else ""

    def walk(value: Any, path: str) -> None:
        if isinstance(value, float) and not math.isfinite(value):
            raise ContractError(f"{where}{path} must be a finite number, got {value!r}")
        if isinstance(value, Mapping):
            for key, inner in value.items():
                walk(inner, f"{path}.{key}" if path else str(key))
        elif isinstance(value, list):
            for i, inner in enumerate(value):
                walk(inner, f"{path}[{i}]")

    for name, rules in (contract.get("columns") or {}).items():
        if not isinstance(rules, Mapping):
            continue
        walk(rules, f"column {name!r}")
        for key in _RATES:
            value: Any = rules.get(key)
            if is_number(value) and not 0 <= value <= 1:
                raise ContractError(
                    f"{where}column {name!r}: {key} must be between 0 and 1, got {value!r}"
                )
    for key, value in contract.items():
        if key != "columns":
            walk(value, str(key))


def json_text(doc: Any) -> str:
    import json

    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def header_meta(table: str, rules: Mapping[str, Any]) -> dict[str, Any]:
    return {"format": FORMAT, "version": VERSION, "table": table, "rules": dict(rules)}


def check_meta(meta: Any) -> None:
    """Refuse metadata written by a newer Shape, or of another format."""
    from . import EmitError

    if not isinstance(meta, Mapping):
        return
    if meta.get("format") not in (None, FORMAT):
        raise EmitError(f"unknown metadata format {meta.get('format')!r}; expected {FORMAT!r}")
    version = meta.get("version")
    if version is None:
        return
    if isinstance(version, bool) or not isinstance(version, int):
        raise EmitError(f"the metadata version must be an integer, got {version!r}")
    if version > VERSION:
        raise EmitError(
            f"{FORMAT} version {version} was written by a newer Shape (this one reads up to "
            f"version {VERSION}); upgrade Shape"
        )
