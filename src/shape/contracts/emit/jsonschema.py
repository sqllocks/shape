"""``shape contract emit --to jsonschema``: a v1 contract as a JSON Schema (draft 2020-12) for
one row, an object with one property per column. A contract with several tables needs ``--table``.

| Contract rule | JSON Schema |
|---|---|
| ``dtype`` | ``type`` (``integer``, ``number`` for float, ``string``, ``boolean``); ``date`` and ``datetime`` are a ``string`` with ``format`` ``date`` / ``date-time`` |
| ``nullable`` | a nullable column's ``type`` also lists ``"null"`` (and its ``enum`` holds ``null``); ``nullable: false`` leaves it out, or is ``{"not": {"type": "null"}}`` when there is no ``dtype`` |
| ``min`` / ``max`` (numbers) | ``minimum`` / ``maximum`` |
| ``allowed_values`` | ``enum`` |
| ``pattern`` (a label) | ``pattern`` (the label's regular expression) |
| ``required_columns`` | ``required`` (a column only named there is a property with no keywords) |
| ``allow_extra_columns: false`` | ``additionalProperties: false`` |

Not expressible, and listed: ``unique`` (a property of the whole table, not of one row),
``max_null_rate``, ``distribution``, ``min_true_rate`` / ``max_true_rate``, ``no_placeholder``,
every table-level rule (``row_count``, ``fd``, ``implies``, ``reference_pair``,
``max_implausible_rate``), a bound that is not a number, an empty ``allowed_values``, an unknown
``dtype`` or pattern label. They are kept in ``x-shape``, on the property for a column and in the
root's ``x-shape.rules`` for the table, which is how :func:`contract_from` rebuilds the whole
contract (``use_meta=True``).
"""  # noqa: E501

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from . import EmitError
from ._common import (
    JOINT_RULES,
    LABELS,
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

SCHEMA_URI = "https://json-schema.org/draft/2020-12/schema"
_TYPES = {
    "integer": ("integer", None),
    "float": ("number", None),
    "string": ("string", None),
    "boolean": ("boolean", None),
    "date": ("string", "date"),
    "datetime": ("string", "date-time"),
}
_DTYPE_OF = {v: k for k, v in _TYPES.items()}
_TABLE_LEVEL = ("row_count", *JOINT_RULES)
_JOINT = "a joint rule relates several columns or rows; a row schema cannot"
_ROW_COUNT = "a row count is a property of the whole table, not of one row"


def _why(rule: str, value: Any) -> str | None:
    if rule == "unique":
        return "uniqueness is a property of the whole table, not of one row"
    if rule == "max_null_rate":
        return "the share of nulls is a property of many rows, not of one row"
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
    return out


def table_schema(table: str, sub: Mapping[str, Any], notes: Notes) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for name, rules in sub.get("columns", {}).items():
        ok, no = split_rules(rules, _why)
        prop = _property(ok)
        if no:
            for rule, reason in no.items():
                notes.add(table, name, rule, reason)
            prop["x-shape"] = {rule: rules[rule] for rule in no}
        properties[name] = prop
    required = list(sub.get("required_columns", []))
    for name in required:
        properties.setdefault(name, {})
    table_rules: dict[str, Any] = {}
    for rule in _TABLE_LEVEL:
        if rule not in sub:
            continue
        reason = _JOINT if rule != "row_count" else why_row_count(sub[rule]) or _ROW_COUNT
        notes.add(table, None, rule, reason)
        table_rules[rule] = sub[rule]
    doc: dict[str, Any] = {
        "$schema": SCHEMA_URI,
        "title": table,
        "type": "object",
        "properties": properties,
        "x-shape": header_meta(table, table_rules),
    }
    if required:
        doc["required"] = required
    if sub.get("allow_extra_columns") is False:
        doc["additionalProperties"] = False
    return doc


def _property(ok: Mapping[str, Any]) -> dict[str, Any]:
    nullable = ok.get("nullable") is not False
    prop: dict[str, Any] = {}
    if "dtype" in ok:
        kind, fmt = _TYPES[ok["dtype"]]
        prop["type"] = [kind, "null"] if nullable else kind
        if fmt:
            prop["format"] = fmt
    elif not nullable:
        prop["not"] = {"type": "null"}
    if "min" in ok:
        prop["minimum"] = ok["min"]
    if "max" in ok:
        prop["maximum"] = ok["max"]
    if "allowed_values" in ok:
        prop["enum"] = [*ok["allowed_values"], *([None] if nullable else [])]
    if "pattern" in ok:
        prop["pattern"] = PATTERNS[ok["pattern"]]
    return prop


def render(tables: Mapping[str, Mapping[str, Any]], notes: Notes) -> str:
    ((name, sub),) = tables.items()
    return json_text(table_schema(name, sub, notes))


def read(text: str, use_meta: bool) -> dict[str, Any]:
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EmitError(f"the text is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise EmitError("a JSON Schema object was expected")
    root_meta = doc.get("x-shape")
    check_meta(root_meta)
    contract: dict[str, Any] = {}
    columns: dict[str, Any] = {}
    for name, prop in (doc.get("properties") or {}).items():
        rules = _read_property(prop) if isinstance(prop, dict) else {}
        if use_meta and isinstance(prop, dict) and isinstance(prop.get("x-shape"), dict):
            rules.update(prop["x-shape"])
        columns[name] = rules
    if columns:
        contract["columns"] = columns
    if doc.get("required"):
        contract["required_columns"] = list(doc["required"])
    if doc.get("additionalProperties") is False:
        contract["allow_extra_columns"] = False
    if use_meta and isinstance(root_meta, dict) and isinstance(root_meta.get("rules"), dict):
        contract.update(root_meta["rules"])
    return normalize_table(contract)


def _read_property(prop: Mapping[str, Any]) -> dict[str, Any]:
    rules: dict[str, Any] = {}
    kind = prop.get("type")
    if kind is not None:
        kinds = kind if isinstance(kind, list) else [kind]
        if "null" not in kinds:
            rules["nullable"] = False
        named = [k for k in kinds if k != "null"]
        if len(named) == 1:
            dtype = _DTYPE_OF.get((named[0], prop.get("format")))
            if dtype:
                rules["dtype"] = dtype
    elif prop.get("not") == {"type": "null"}:
        rules["nullable"] = False
    if "minimum" in prop:
        rules["min"] = prop["minimum"]
    if "maximum" in prop:
        rules["max"] = prop["maximum"]
    if isinstance(prop.get("enum"), list):
        rules["allowed_values"] = [v for v in prop["enum"] if v is not None]
    if prop.get("pattern") in LABELS:
        rules["pattern"] = LABELS[prop["pattern"]]
    return rules
