"""Contract rules on the joint analysis of a profile (#47): ``fd``, ``implies``,
``max_implausible_rate`` and the column rules ``no_placeholder`` and ``valid_as`` (W3-12).

They read what ``shape.profile`` stored (a table's ``joint`` entry and a column's
``placeholders``). A rule the profile holds no evidence for is a violation that says "not
measured", so a contract never passes a rule it did not test.
"""

from __future__ import annotations

from typing import Any

MIN_REPORTED_CONFIDENCE = 0.8  # profiles list dependencies at or above this confidence


def _violation(column: str | None, rule: str, expected: Any, observed: Any) -> dict[str, Any]:
    return {"column": column, "rule": rule, "expected": expected, "observed": observed}


def check_no_placeholder(name: str, rule: Any, col: dict[str, Any]) -> list[dict[str, Any]]:
    """``no_placeholder`` on a column: true (none at all), or an object with ``max_share`` and
    ``allow``."""
    if rule is False:
        return []
    max_share = 0.0 if rule is True else float(rule.get("max_share", 0.0))
    allow = set() if rule is True else {str(v) for v in rule.get("allow", ())}
    found = [
        p
        for p in col.get("placeholders") or ()
        if str(p["value"]) not in allow and p["share"] > max_share
    ]
    if not found:
        return []
    return [
        _violation(
            name,
            "no_placeholder",
            {"max_share": max_share, "allow": sorted(allow)},
            [
                {"value": p["value"], "kind": p["kind"], "share": p["share"], "count": p["count"]}
                for p in found
            ],
        )
    ]


def check_valid_as(name: str, rule: dict[str, Any], col: dict[str, Any]) -> list[dict[str, Any]]:
    """``valid_as`` on a column: the share of values that are valid codes of ``kind`` (measured
    by ``shape.profile(..., validators=)``) is at least ``min_valid_rate`` (default 1.0)."""
    kind = rule["kind"]
    minimum = float(rule.get("min_valid_rate", 1.0))
    expected = {"kind": kind, "min_valid_rate": minimum}
    measured = (col.get("validators") or {}).get(kind)
    if measured is None:
        return [
            _violation(
                name,
                "valid_as",
                expected,
                f"not measured: profile with validators={{{name!r}: {kind!r}}} "
                f"(shape profile --validate {name}={kind})",
            )
        ]
    rate = measured.get("valid_rate")
    if rate is None or rate >= minimum:  # nothing to check: no value is invalid
        return []
    return [
        _violation(
            name,
            "valid_as",
            expected,
            {k: measured[k] for k in ("checked", "valid", "valid_rate")},
        )
    ]


def _joint(table: dict[str, Any]) -> dict[str, Any] | None:
    j = table.get("joint")
    return j if isinstance(j, dict) else None


def _names(value: Any) -> list[str]:
    return [value] if isinstance(value, str) else list(value)


def check_fd(rule: dict[str, Any], table: dict[str, Any]) -> list[dict[str, Any]]:
    det = _names(rule["determinant"])
    dep = rule["dependent"]
    label = f"{', '.join(det)} -> {dep}"
    minimum = float(rule["min_confidence"])
    expected = {"min_confidence": minimum}
    columns = table["columns"]
    missing = [c for c in (*det, dep) if c not in columns]
    if missing:
        return [_violation(label, "fd", expected, {"missing_columns": missing})]
    if len(det) == 1 and columns[det[0]].get("is_unique") is True:
        return []  # a unique determinant fixes every other column
    j = _joint(table)
    if j is None:
        return [
            _violation(label, "fd", expected, "not measured: the profile has no joint analysis")
        ]
    for entry in j.get("dependencies", ()):
        if entry["determinant"] == det and entry["dependent"] == dep:
            if entry["confidence"] >= minimum:
                return []
            return [
                _violation(
                    label,
                    "fd",
                    expected,
                    {
                        "confidence": entry["confidence"],
                        "violating_groups": entry["violating_groups"],
                        "groups": entry["groups"],
                        "violations": entry.get("violations", []),
                    },
                )
            ]
    analysed = set(j.get("columns", ()))
    if len(det) == 1 and det[0] in analysed and dep in analysed:
        if minimum > MIN_REPORTED_CONFIDENCE:
            return [
                _violation(
                    label,
                    "fd",
                    expected,
                    {"confidence_below": MIN_REPORTED_CONFIDENCE, "violations": []},
                )
            ]
        return [
            _violation(
                label,
                "fd",
                expected,
                f"not measured: the profile lists dependencies of confidence "
                f"{MIN_REPORTED_CONFIDENCE} or more",
            )
        ]
    return [
        _violation(
            label,
            "fd",
            expected,
            "not measured: the dependency was not analysed (single-column determinants "
            "among the profile's analysed columns only)",
        )
    ]


def check_implies(rule: dict[str, Any], table: dict[str, Any]) -> list[dict[str, Any]]:
    a, b = rule["if"], rule["then"]
    label = f"{a['column']}={a['equals']!r} => {b['column']}={b['equals']!r}"
    minimum = float(rule["min_confidence"])
    expected = {"min_confidence": minimum}
    j = _joint(table)
    cond = None
    for c in (j or {}).get("conditionals", ()):
        if c["given"] == a["column"] and c["target"] == b["column"]:
            cond = c
            break
    if cond is None:
        return [
            _violation(
                label,
                "implies",
                expected,
                "not measured: the profile holds no conditional table for these columns "
                "(strongly associated categorical pairs of few values only)",
            )
        ]
    row = cond["table"].get(str(a["equals"]))
    if row is None:
        return [
            _violation(label, "implies", expected, "not measured: the value is not in the table")
        ]
    probs = row["p"]
    p = probs.get(str(b["equals"]))
    if p is None:  # not among the listed top values: at most the smallest listed
        p = 0.0 if len(probs) < 8 else min(probs.values())
    if p >= minimum:
        return []
    return [_violation(label, "implies", expected, {"confidence": p, "rows": row["n"]})]


def check_reference_pair(rule: dict[str, Any], table: dict[str, Any]) -> list[dict[str, Any]]:
    cols = _names(rule["columns"])
    ref = rule["reference"]
    label = f"{', '.join(cols)} in {ref}"
    minimum = float(rule["min_match_rate"])
    expected = {"min_match_rate": minimum}
    for entry in (_joint(table) or {}).get("reference_pairs", ()):
        if entry["columns"] == cols and ref in (entry["reference"], entry["name"]):
            rate = entry["match_rate"]
            if rate is not None and rate >= minimum:
                return []
            return [
                _violation(
                    label,
                    "reference_pair",
                    expected,
                    {
                        "match_rate": rate,
                        "mismatched": entry["mismatched"],
                        "rows": entry["rows"],
                        "examples": entry["examples"],
                    },
                )
            ]
    return [
        _violation(
            label,
            "reference_pair",
            expected,
            "not measured: profile with reference_pairs=[{'columns': ..., 'reference': ...}] "
            "(shape profile --reference-pair)",
        )
    ]


def check_implausible(rate: float, table: dict[str, Any]) -> list[dict[str, Any]]:
    j = _joint(table)
    if j is None or j.get("implausible_rate") is None:
        return [
            _violation(
                None,
                "max_implausible_rate",
                rate,
                "not measured: the profile has no joint analysis",
            )
        ]
    if j["implausible_rate"] <= rate:
        return []
    return [
        _violation(
            None,
            "max_implausible_rate",
            rate,
            {
                "implausible_rate": j["implausible_rate"],
                "by_dependency": j.get("implausible_by_dependency"),
                "by_placeholder": j.get("implausible_by_placeholder"),
            },
        )
    ]


def check_joint_rules(contract: dict[str, Any], table: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rule in contract.get("fd", ()):
        out.extend(check_fd(rule, table))
    for rule in contract.get("implies", ()):
        out.extend(check_implies(rule, table))
    for rule in contract.get("reference_pair", ()):
        out.extend(check_reference_pair(rule, table))
    if "max_implausible_rate" in contract:
        out.extend(check_implausible(float(contract["max_implausible_rate"]), table))
    return out
