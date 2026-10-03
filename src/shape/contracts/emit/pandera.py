"""``shape contract emit --to pandera``: a v1 contract as Python source that defines a
``pandera.DataFrameSchema`` per table, in a dict ``SCHEMAS`` keyed by table name. The text is
generated, not built with pandera: emitting does not import pandera or pandas.

| Contract rule | pandera |
|---|---|
| ``dtype`` | ``Column(dtype)``: ``integer`` ``"int64"`` (``"Int64"`` when nullable, as numpy's integers cannot hold a null), ``float`` ``"float64"``, ``string`` ``"str"``, ``boolean`` ``"bool"`` (``"boolean"`` when nullable), ``datetime`` ``"datetime64[ns]"``, ``date`` ``pa.Date`` |
| ``nullable`` | ``nullable=`` (pandera's default is not nullable, so a column that may be null says ``True``) |
| ``unique: true`` | ``unique=True`` |
| ``min`` and ``max`` (numbers) | ``Check.in_range(min, max)``; ``Check.ge(min)`` / ``Check.le(max)`` for one bound |
| ``allowed_values`` | ``Check.isin([...])`` (a null is dropped) |
| ``pattern`` (a label) | ``Check.str_matches(regex)`` |
| ``row_count`` ``min`` / ``max`` | a table ``Check`` on ``len(df)`` |
| ``required_columns`` | ``required=True`` (a column the contract lists but does not require is ``required=False``) |
| ``allow_extra_columns: false`` | ``strict=True`` |

Not expressible, and listed: ``max_null_rate`` (a ``Column`` has no tolerated share of nulls),
``distribution``, ``min_true_rate`` / ``max_true_rate``, ``no_placeholder``, the joint rules, a
bound that is not a number, an empty ``allowed_values``, an unknown ``dtype`` or pattern label.
They are kept in ``metadata={"shape": ...}`` on the column or the schema.
"""  # noqa: E501

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

from ._common import (
    FORMAT,
    JOINT_RULES,
    PATTERNS,
    VERSION,
    Notes,
    split_rules,
    why_common,
    why_row_count,
)

_DTYPES = {
    "integer": ('"Int64"', '"int64"'),
    "float": ('"float64"', '"float64"'),
    "string": ('"str"', '"str"'),
    "boolean": ('"boolean"', '"bool"'),
    "datetime": ('"datetime64[ns]"', '"datetime64[ns]"'),
    "date": ("pa.Date", "pa.Date"),
}


def _why(rule: str, value: Any) -> str | None:
    if rule == "max_null_rate":
        return "a pandera Column has no tolerated share of nulls"
    return why_common(rule, value)


def py(value: Any, indent: int = 0) -> str:
    """A Python literal for JSON-like ``value``: sorted dict keys, the same text every run."""
    if isinstance(value, bool) or value is None:
        return repr(value)
    if isinstance(value, float):
        return repr(value) if math.isfinite(value) else f"float({str(value)!r})"
    if isinstance(value, (int, str)):
        return json.dumps(value, ensure_ascii=False) if isinstance(value, str) else repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(py(v) for v in value) + "]"
    if isinstance(value, Mapping):
        items = ", ".join(f"{py(str(k))}: {py(v)}" for k, v in sorted(value.items()))
        return "{" + items + "}"
    return py(str(value))


def _column(name: str, rules: Mapping[str, Any], required: bool, notes: Notes, table: str) -> str:
    ok, no = split_rules(rules, _why)
    for rule, reason in no.items():
        notes.add(table, name, rule, reason)
    nullable = ok.get("nullable") is not False
    args: list[str] = []
    if "dtype" in ok:
        args.append(_DTYPES[ok["dtype"]][0 if nullable else 1])
    args.append(f"nullable={nullable}")
    if ok.get("unique") is True:
        args.append("unique=True")
    checks: list[str] = []
    lo, hi = ok.get("min"), ok.get("max")
    if lo is not None and hi is not None:
        checks.append(f"Check.in_range({py(lo)}, {py(hi)})")
    elif lo is not None:
        checks.append(f"Check.ge({py(lo)})")
    elif hi is not None:
        checks.append(f"Check.le({py(hi)})")
    if "allowed_values" in ok:
        checks.append(f"Check.isin({py([v for v in ok['allowed_values'] if v is not None])})")
    if "pattern" in ok:
        checks.append(f"Check.str_matches({py(PATTERNS[ok['pattern']])})")
    if checks:
        args.append("checks=[" + ", ".join(checks) + "]")
    if not required:
        args.append("required=False")
    if no:
        args.append(f"metadata={py({'shape': {rule: rules[rule] for rule in no}})}")
    return f"{py(name)}: Column({', '.join(args)})"


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


def table_schema(table: str, sub: Mapping[str, Any], notes: Notes) -> str:
    required = set(sub.get("required_columns", []))
    names = list(sub.get("columns", {}))
    names += [c for c in sorted(required) if c not in names]
    columns = [
        "            " + _column(n, sub.get("columns", {}).get(n, {}), n in required, notes, table)
        for n in names
    ]
    lines = [f"    {py(table)}: DataFrameSchema(", "        columns={"]
    lines += [c + "," for c in columns]
    lines.append("        },")
    meta: dict[str, Any] = {}
    rc = sub.get("row_count")
    if rc is not None:
        reason = why_row_count(rc)
        if reason:
            notes.add(table, None, "row_count", reason)
            meta["row_count"] = rc
        elif rc:
            lo, hi = rc.get("min"), rc.get("max")
            if lo is not None and hi is not None:
                cond, text = f"{py(lo)} <= len(df) <= {py(hi)}", f"{lo} to {hi}"
            elif lo is not None:
                cond, text = f"len(df) >= {py(lo)}", f"at least {lo}"
            else:
                cond, text = f"len(df) <= {py(hi)}", f"at most {hi}"
            lines.append(
                f'        checks=[Check(lambda df: {cond}, name="shape_row_count", '
                f"error={py('row count must be ' + text)})],"
            )
    for rule in JOINT_RULES:
        if rule in sub:
            notes.add(
                table,
                None,
                rule,
                "a joint rule relates several columns or rows; pandera has no such check here",
            )
            meta[rule] = sub[rule]
    if sub.get("allow_extra_columns") is False:
        lines.append("        strict=True,")
    lines.append(f"        name={py(table)},")
    if meta:
        lines.append(f"        metadata={py({'shape': meta})},")
    lines.append("    ),")
    return "\n".join(lines)


def render(tables: Mapping[str, Mapping[str, Any]], notes: Notes) -> str:
    head = [
        f"# Contract emitted by Shape ({FORMAT}, version {VERSION}).",
        "# Regenerate with `shape contract emit`; do not edit.",
        "try:",
        "    import pandera.pandas as pa",
        "except ImportError:  # pandera before 0.24 has no pandera.pandas",
        "    import pandera as pa",
        "",
        "Check, Column, DataFrameSchema = pa.Check, pa.Column, pa.DataFrameSchema",
        "",
        "SCHEMAS = {",
    ]
    body = [table_schema(name, sub, notes) for name, sub in tables.items()]
    return "\n".join([*head, *body, "}", ""]) if body else "\n".join([*head, "}", ""])
