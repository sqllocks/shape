"""Drift of the joint analysis (#47): a broken dependency, a placeholder surge, a rise in the
share of implausible rows and a shift in an association, each naming the columns and the value.

The input is the ``joint`` entry and the ``placeholders`` of a table profile
(``shape.profile.joint``); a profile without them (an older one, a window of the stream profiler)
has nothing to compare and yields no change here.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .engine import Policy, TableView, View

MIN_REPORTED_CONFIDENCE = 0.8  # profiles list dependencies at or above this confidence
_ASSOC_KEYS = (
    "cramers_v",
    "theil_u_a_given_b",
    "theil_u_b_given_a",
    "pearson",
    "spearman",
    "correlation_ratio",
)


def _skipped(policy: Policy, scope: str | None, columns: list[str]) -> bool:
    """A change on several columns is dropped when any is ignored, and kept when any is selected."""
    if policy.ignore and any(policy._matches(policy.ignore, scope, c) for c in columns):
        return True
    return bool(policy.only) and not any(policy._matches(policy.only, scope, c) for c in columns)


def _record(
    column: str,
    kind: str,
    baseline: Any,
    current: Any,
    score: float,
    detail: dict[str, Any],
    message: str,
) -> dict[str, Any]:
    from .engine import KIND_SEVERITY

    return {
        "column": column,
        "kind": kind,
        "baseline": baseline,
        "current": current,
        "severity": KIND_SEVERITY[kind],
        "score": round(min(1.0, max(0.0, float(score))), 4),
        "message": message,
        "detail": detail,
    }


def _label(table: str | None, name: str) -> str:
    return f"{table}.{name}" if table else name


def _fd_key(entry: Mapping[str, Any]) -> tuple[tuple[str, ...], str]:
    return tuple(entry["determinant"]), str(entry["dependent"])


def _noise(confidence: float, n_base: int, n_cur: int) -> float:
    """Three standard errors of a confidence measured on two samples: a drop inside it is not
    evidence of a change."""
    p = min(max(confidence, 0.0), 1.0)
    return 3.0 * math.sqrt(max(p * (1.0 - p), 0.0) * (1.0 / max(n_base, 1) + 1.0 / max(n_cur, 1)))


def _placeholder_in(view: View | None, value: str) -> dict[str, Any] | None:
    if view is None:
        return None
    for p in view.placeholders:
        if str(p["value"]) == value:
            return p
    return None


def _dependencies(
    table: str | None,
    bt: TableView,
    ct: TableView,
    th: Mapping[str, Any],
    policy: Policy,
) -> list[dict[str, Any]]:
    bj, cj = bt.joint or {}, ct.joint or {}
    if not cj.get("dependencies") and not bj.get("dependencies"):
        return []
    n_base, n_cur = int(bj.get("rows_analyzed", 0)), int(cj.get("rows_analyzed", 0))
    base = {_fd_key(e): e for e in bj.get("dependencies", ())}
    cur = {_fd_key(e): e for e in cj.get("dependencies", ())}
    out: list[dict[str, Any]] = []

    def emit(det: str, dep: str, b_conf: float, basis: str, c: Mapping[str, Any] | None) -> None:
        c_conf = None if c is None else float(c["confidence"])
        effective = c_conf if c_conf is not None else MIN_REPORTED_CONFIDENCE
        drop = b_conf - effective
        floor = max(th["dependency_confidence"], _noise(b_conf, n_base, n_cur))
        if drop <= floor:
            return
        violations = [] if c is None else list(c.get("violations", ()))
        causes = []
        for v in violations:
            ph = _placeholder_in(ct.columns.get(det), str(v["determinant_value"]))
            if ph is not None:
                causes.append(
                    {
                        "value": ph["value"],
                        "kind": ph["kind"],
                        "rows": v["rows"],
                        "share_of_rows": ph["share"],
                    }
                )
        detail: dict[str, Any] = {
            "determinant": [det],
            "dependent": dep,
            "baseline_confidence": round(b_conf, 6),
            "baseline_basis": basis,
            "current_confidence": None if c_conf is None else round(c_conf, 6),
            "current_confidence_below": MIN_REPORTED_CONFIDENCE if c is None else None,
            "violating_groups": None if c is None else c["violating_groups"],
            "groups": None if c is None else c["groups"],
            "violations": violations,
            "placeholders": causes,
        }
        shown = "<" + format(MIN_REPORTED_CONFIDENCE, ".3f") if c_conf is None else f"{c_conf:.3f}"
        msg = f"{det} no longer determines {dep}: confidence {b_conf:.3f} -> {shown}"
        if c is not None:
            msg += f" ({c['violating_groups']} violating groups"
            if violations:
                v0 = violations[0]
                msg += f"; worst: {det}={v0['determinant_value']!r} maps to {v0['distinct_dependents']} {dep} values"
            msg += ")"
        if causes:
            msg += f"; placeholder {causes[0]['value']!r} in {det} ({causes[0]['share_of_rows']:.1%} of rows)"
        if basis == "key":
            msg += f" ({det} was unique, so the dependency held trivially)"
        out.append(
            _record(
                _label(table, f"{det} -> {dep}"),
                "dependency_broken",
                round(b_conf, 6),
                None if c_conf is None else round(c_conf, 6),
                drop,
                detail,
                msg,
            )
        )

    for key, b in base.items():
        det, dep = key[0][0], key[1]
        if len(key[0]) != 1 or _skipped(policy, table, [det, dep]):
            continue
        if det not in ct.columns or dep not in ct.columns:
            continue  # a removed column is a column change already
        emit(det, dep, float(b["confidence"]), "dependency", cur.get(key))
    for key, c in cur.items():
        det, dep = key[0][0], key[1]
        if key in base or len(key[0]) != 1 or _skipped(policy, table, [det, dep]):
            continue
        bv = bt.columns.get(det)
        if bv is None or dep not in bt.columns:
            continue
        if bv.unique_like:  # a unique determinant determined every column, trivially
            emit(det, dep, 1.0, "key", c)
    return out


def _placeholders(
    table: str | None, bt: TableView, ct: TableView, th: Mapping[str, Any], policy: Policy
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name, cv in ct.columns.items():
        bv = bt.columns.get(name)
        if bv is None or not cv.placeholders or _skipped(policy, table, [name]):
            continue
        for p in cv.placeholders:
            value = str(p["value"])
            known = _placeholder_in(bv, value)
            if known is not None:
                before = float(known["share"])
            elif bv.top_values and value in bv.top_values:
                before = float(bv.top_values[value]) * (1.0 - (bv.null_rate or 0.0))
            else:
                before = 0.0
            now = float(p["share"])
            if now - before <= th["placeholder_share"]:
                continue
            msg = (
                f"{name}: placeholder {value!r} ({str(p['kind']).replace('_', ' ')}) went from "
                f"{before:.1%} to {now:.1%} of rows"
            )
            out.append(
                _record(
                    _label(table, name),
                    "placeholder_surge",
                    round(before, 6),
                    round(now, 6),
                    now - before,
                    {
                        "value": value,
                        "placeholder_kind": p["kind"],
                        "baseline_share": round(before, 6),
                        "current_share": round(now, 6),
                        "rows": p["count"],
                        "evidence": p["evidence"],
                    },
                    msg,
                )
            )
    return out


def _implausible(
    table: str | None, bt: TableView, ct: TableView, th: Mapping[str, Any]
) -> list[dict[str, Any]]:
    b = (bt.joint or {}).get("implausible_rate")
    c = (ct.joint or {}).get("implausible_rate")
    if b is None or c is None or c - b <= th["implausible_rate"]:
        return []
    cj = ct.joint or {}
    msg = f"implausible rows went from {b:.1%} to {c:.1%}"
    return [
        _record(
            _label(table, "(rows)"),
            "implausible_rate_change",
            b,
            c,
            c - b,
            {
                "by_dependency": cj.get("implausible_by_dependency"),
                "by_placeholder": cj.get("implausible_by_placeholder"),
            },
            msg,
        )
    ]


def _associations(
    table: str | None, bt: TableView, ct: TableView, th: Mapping[str, Any], policy: Policy
) -> list[dict[str, Any]]:
    base = {(e["a"], e["b"], e["kind"]): e for e in (bt.joint or {}).get("associations", ())}
    out: list[dict[str, Any]] = []
    for e in (ct.joint or {}).get("associations", ()):
        b = base.get((e["a"], e["b"], e["kind"]))
        if b is None or _skipped(policy, table, [e["a"], e["b"]]):
            continue
        for key in _ASSOC_KEYS:
            if key not in e or b.get(key) is None or e[key] is None:
                continue
            if abs(abs(e[key]) - abs(b[key])) > th["association_shift"]:
                out.append(
                    _record(
                        _label(table, f"{e['a']} ~ {e['b']}"),
                        "association_shift",
                        b[key],
                        e[key],
                        abs(abs(e[key]) - abs(b[key])),
                        {"measure": key, "kind": e["kind"]},
                        f"{e['a']} ~ {e['b']}: {key} {b[key]:.3f} -> {e[key]:.3f}",
                    )
                )
    return out


def diff_joint(
    table: str | None, bt: TableView, ct: TableView, policy: Policy
) -> list[tuple[str | None, str | None, dict[str, Any]]]:
    """Joint changes between two tables as ``(table, column, record)`` (the engine's shape)."""
    from .engine import SEVERITY_RANK

    th = policy.for_column(table, None)
    records = (
        _dependencies(table, bt, ct, th, policy)
        + _placeholders(table, bt, ct, th, policy)
        + _implausible(table, bt, ct, th)
        + _associations(table, bt, ct, th, policy)
    )
    floor = SEVERITY_RANK[th["min_severity"]]
    return [(table, None, r) for r in records if SEVERITY_RANK[r["severity"]] >= floor]
