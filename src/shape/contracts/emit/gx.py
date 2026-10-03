"""``shape contract emit --to gx``: a v1 contract as a Great Expectations 1.x expectation suite
(JSON). A contract with several tables needs ``--table``; the suite is named after the table.

| Contract rule | Expectation |
|---|---|
| ``required_columns`` | ``expect_column_to_exist`` per column |
| ``nullable: false`` | ``expect_column_values_to_not_be_null`` |
| ``max_null_rate`` | ``expect_column_values_to_not_be_null`` with ``mostly`` = 1 - rate |
| ``unique: true`` | ``expect_column_values_to_be_unique`` |
| ``min`` / ``max`` (numbers) | ``expect_column_values_to_be_between`` (``min_value`` / ``max_value``) |
| ``allowed_values`` | ``expect_column_values_to_be_in_set`` (``value_set``; a null is dropped) |
| ``pattern`` (a label) | ``expect_column_values_to_match_regex`` with ``mostly`` 0.9, the share the profiler needs to give the label |
| ``row_count`` ``min`` / ``max`` | ``expect_table_row_count_to_be_between`` |
| ``allow_extra_columns: false`` | ``expect_table_columns_to_match_set`` (``exact_match``) |

Not expressible, and listed: ``dtype`` (a Great Expectations type name depends on the backend),
``distribution``, ``min_true_rate`` / ``max_true_rate``, ``no_placeholder``, the joint rules, a bound
that is not a number, an empty ``allowed_values``, an unknown pattern label. They are kept in the
suite's ``meta.shape`` (``columns`` and ``rules``), which :func:`contract_from` reads with
``use_meta=True``. Every expectation names the contract rule it came from in ``meta.shape_rule``.
The suite holds no id and no timestamp: the same contract gives the same bytes.
"""  # noqa: E501

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from . import EmitError
from ._common import (
    JOINT_RULES,
    LABELS,
    NULL_RATE_PLACES,
    PATTERNS,
    Notes,
    check_meta,
    header_meta,
    json_text,
    normalize_table,
    split_rules,
    why_common,
    why_row_count,
)

REGEX_MOSTLY = 0.9
_TABLE_LEVEL = JOINT_RULES


def _why(rule: str, value: Any) -> str | None:
    if rule == "dtype":
        return "a Great Expectations type name depends on the backend (pandas, Spark, SQL)"
    return why_common(rule, value)


def expressed_table(sub: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, rules in sub.get("columns", {}).items():
        ok, _ = split_rules(rules, _why)
        if ok:
            out.setdefault("columns", {})[name] = ok
    for key in ("required_columns", "allow_extra_columns"):
        if key in sub:
            out[key] = sub[key]
    if "row_count" in sub and why_row_count(sub["row_count"]) is None:
        out["row_count"] = sub["row_count"]
    return out


def _expectation(type_: str, kwargs: dict[str, Any], rule: str) -> dict[str, Any]:
    return {"type": type_, "kwargs": kwargs, "meta": {"shape_rule": rule}}


def table_suite(table: str, sub: Mapping[str, Any], notes: Notes) -> dict[str, Any]:
    expectations: list[dict[str, Any]] = []
    for name in sorted(sub.get("required_columns", [])):
        expectations.append(_expectation("expect_column_to_exist", {"column": name}, "required"))
    rc = sub.get("row_count")
    table_rules: dict[str, Any] = {}
    if rc is not None:
        reason = why_row_count(rc)
        if reason:
            notes.add(table, None, "row_count", reason)
            table_rules["row_count"] = rc
        elif rc:
            expectations.append(
                _expectation(
                    "expect_table_row_count_to_be_between",
                    {f"{k}_value": rc[k] for k in ("min", "max") if k in rc},
                    "row_count",
                )
            )
    if sub.get("allow_extra_columns") is False:
        known = sorted({*sub.get("columns", {}), *sub.get("required_columns", [])})
        expectations.append(
            _expectation(
                "expect_table_columns_to_match_set",
                {"column_set": known, "exact_match": True},
                "allow_extra_columns",
            )
        )
    column_meta: dict[str, Any] = {}
    for name, rules in sub.get("columns", {}).items():
        ok, no = split_rules(rules, _why)
        for rule, reason in no.items():
            notes.add(table, name, rule, reason)
        if no:
            column_meta[name] = {rule: rules[rule] for rule in no}
        expectations.extend(_column_expectations(name, ok))
    for rule in _TABLE_LEVEL:
        if rule in sub:
            notes.add(
                table,
                None,
                rule,
                "a joint rule relates several columns or rows; no expectation does",
            )
            table_rules[rule] = sub[rule]
    meta = header_meta(table, table_rules)
    meta["columns"] = column_meta
    return {"name": table, "expectations": expectations, "meta": {"shape": meta}}


def _column_expectations(name: str, ok: Mapping[str, Any]) -> list[dict[str, Any]]:
    col = {"column": name}
    out: list[dict[str, Any]] = []
    if ok.get("nullable") is False:
        out.append(_expectation("expect_column_values_to_not_be_null", dict(col), "nullable"))
    if "max_null_rate" in ok:
        mostly = round(1 - float(ok["max_null_rate"]), NULL_RATE_PLACES)
        out.append(
            _expectation(
                "expect_column_values_to_not_be_null",
                {**col, "mostly": mostly},
                "max_null_rate",
            )
        )
    if ok.get("unique") is True:
        out.append(_expectation("expect_column_values_to_be_unique", dict(col), "unique"))
    if "min" in ok or "max" in ok:
        bounds = {f"{k}_value": ok[k] for k in ("min", "max") if k in ok}
        out.append(_expectation("expect_column_values_to_be_between", {**col, **bounds}, "min_max"))
    if "allowed_values" in ok:
        out.append(
            _expectation(
                "expect_column_values_to_be_in_set",
                {**col, "value_set": list(ok["allowed_values"])},
                "allowed_values",
            )
        )
    if "pattern" in ok:
        out.append(
            _expectation(
                "expect_column_values_to_match_regex",
                {**col, "regex": PATTERNS[ok["pattern"]], "mostly": REGEX_MOSTLY},
                "pattern",
            )
        )
    return out


def render(tables: Mapping[str, Mapping[str, Any]], notes: Notes) -> str:
    ((name, sub),) = tables.items()
    return json_text(table_suite(name, sub, notes))


def read(text: str, use_meta: bool) -> dict[str, Any]:
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EmitError(f"the text is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("expectations"), list):
        raise EmitError(
            "a Great Expectations suite object with an 'expectations' list was expected"
        )
    meta = (doc.get("meta") or {}).get("shape") if isinstance(doc.get("meta"), dict) else None
    check_meta(meta)
    columns: dict[str, dict[str, Any]] = {}
    contract: dict[str, Any] = {}
    required: list[str] = []
    for exp in doc["expectations"]:
        if not isinstance(exp, dict):
            continue
        kwargs = exp.get("kwargs") or {}
        tag = (exp.get("meta") or {}).get("shape_rule")
        col = kwargs.get("column")
        rules = columns.setdefault(col, {}) if isinstance(col, str) else {}
        kind = exp.get("type")
        if kind == "expect_column_to_exist" and isinstance(col, str):
            required.append(col)
        elif kind == "expect_column_values_to_not_be_null":
            mostly = kwargs.get("mostly")
            rate: float | None = None
            if isinstance(mostly, (int, float)) and not isinstance(mostly, bool):
                if tag == "max_null_rate" or (tag is None and mostly < 1):
                    rate = round(1 - float(mostly), NULL_RATE_PLACES)
            if rate is not None:
                rules["max_null_rate"] = rate
            else:
                rules["nullable"] = False
        elif kind == "expect_column_values_to_be_unique":
            rules["unique"] = True
        elif kind == "expect_column_values_to_be_between":
            for rule in ("min", "max"):
                if f"{rule}_value" in kwargs and kwargs[f"{rule}_value"] is not None:
                    rules[rule] = kwargs[f"{rule}_value"]
        elif kind == "expect_column_values_to_be_in_set":
            rules["allowed_values"] = list(kwargs.get("value_set") or [])
        elif kind == "expect_column_values_to_match_regex":
            if kwargs.get("regex") in LABELS:
                rules["pattern"] = LABELS[kwargs["regex"]]
        elif kind == "expect_table_row_count_to_be_between":
            contract["row_count"] = {
                k: kwargs[f"{k}_value"]
                for k in ("min", "max")
                if kwargs.get(f"{k}_value") is not None
            }
        elif kind == "expect_table_columns_to_match_set":
            contract["allow_extra_columns"] = False
    contract["columns"] = {c: r for c, r in columns.items() if r}
    if required:
        contract["required_columns"] = required
    if use_meta and isinstance(meta, dict):
        for name, rules in (meta.get("columns") or {}).items():
            contract["columns"].setdefault(name, {}).update(rules)
        contract.update(meta.get("rules") or {})
    return normalize_table(contract)
