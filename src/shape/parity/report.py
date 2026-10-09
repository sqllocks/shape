"""The parity report (``shape-parity-report``, version 1) and its text form."""

from __future__ import annotations

from typing import Any

from .checks import CATEGORIES, FAIL, NOT_MEASURED, PASS, Check
from .sides import Side

FORMAT = "shape-parity-report"
VERSION = 1


def build_report(
    a: Side, b: Side, checks: list[Check], options: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build a structured environment parity result report."""
    by_category = {c: {PASS: 0, FAIL: 0, NOT_MEASURED: 0} for c in CATEGORIES}
    for check in checks:
        by_category[check.category][check.status] += 1
    summary = {s: sum(v[s] for v in by_category.values()) for s in (PASS, FAIL, NOT_MEASURED)}
    return {
        "format": FORMAT,
        "version": VERSION,
        "inputs": {"a": a.describe(), "b": b.describe()},
        "options": options or {},
        "summary": {**summary, "by_category": by_category},
        "checks": [c.to_dict() for c in checks],
        "parity": summary[FAIL] == 0,
    }


def _where(check: dict[str, Any]) -> str:
    table, column = check.get("table"), check.get("column")
    if table and column:
        return f"{table}.{column}"
    return str(table or column or "")


def _explain(check: dict[str, Any]) -> str:
    d = check.get("details") or {}
    if "reason" in d:
        extra = ""
        if check["category"] == "relationships":
            extra = f" ({d.get('parent')} <- {', '.join(d.get('child_columns') or [])})"
        return str(d["reason"]) + extra
    if "changes" in d:
        return "; ".join(f"{c['kind']} ({c['baseline']} -> {c['current']})" for c in d["changes"])
    if "a" in d and "b" in d:
        text = f"A {d['a']}, B {d['b']}"
        if "share_a" in d:
            text = f"share A {d['share_a']}, B {d['share_b']} (rows {d['a']} and {d['b']})"
        if d.get("difference") is not None and "tolerance" in d:
            text += f", difference {d['difference']} above {d['tolerance']}"
        elif d.get("difference") is not None and "threshold" in d:
            text += f", difference {d['difference']} above {d['threshold']}"
        return text
    return ""


def render_text(report: dict[str, Any]) -> str:
    """Failures first, then what could not be measured, then a count of what passed."""
    lines: list[str] = []
    ins = report["inputs"]
    for side in ("a", "b"):
        i = ins[side]
        lines.append(f"{side.upper()}: {i['path']} ({i['kind']}, {i['content_id'][:12]})")
    checks = report["checks"]
    for status, title in ((FAIL, "FAIL"), (NOT_MEASURED, "NOT MEASURED")):
        rows = [c for c in checks if c["status"] == status]
        if not rows:
            continue
        lines.append("")
        lines.append(f"{title} ({len(rows)})")
        for c in rows:
            owner = f" [owner: {c['owner']}]" if c.get("owner") else ""
            lines.append(f"  {c['category']:<13} {_where(c)}: {_explain(c)}{owner}".rstrip())
    s = report["summary"]
    lines.append("")
    verdict = "parity" if report["parity"] else "NO parity"
    lines.append(f"{verdict}: {s[PASS]} passed, {s[FAIL]} failed, {s[NOT_MEASURED]} not measured")
    return "\n".join(lines) + "\n"
