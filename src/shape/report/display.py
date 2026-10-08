"""Notebook display (``_repr_html_`` and ``_repr_markdown_``) for profiles, diffs, check results
and drift reports.

Small by construction: a table is capped at ``MAX_ROWS`` rows with a "more" line, and every cell is
short. Safe values only: a profile shows names, types, null rates and distinct counts, plus a mean
for a column the safe-profile rules do not reduce to a pattern, and never an extreme, a category or
a top value. A diff goes through :func:`shape.report.explain.explain`, and a check result shows an
expected or observed value only for a rule that compares an aggregate. Everything rendered is
escaped: HTML with ``html.escape``, Markdown by backslash-escaping its markup and ``<``.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping, Sequence
from typing import Any

MAX_ROWS = 25
_MAX_CELL = 60

_STYLE = "border-collapse:collapse;font:13px system-ui,sans-serif;"
_CELL = "border:1px solid #9aa5b1;padding:2px 8px;text-align:left"

# A check rule whose expected and observed values are aggregates; every other rule compares values.
_AGGREGATE_RULES = frozenset(
    {
        "dtype",
        "nullable",
        "unique",
        "max_null_rate",
        "distribution",
        "min_true_rate",
        "max_true_rate",
        "row_count",
    }
)


def _short(value: Any) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= _MAX_CELL else text[: _MAX_CELL - 1] + "…"


def _num(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return "" if value is None else str(value)
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def _e(value: Any) -> str:
    return html.escape(_short(value), quote=True)


def _md(value: Any) -> str:
    """Markdown text with its markup neutralised: ``&``, ``<`` and ``>`` become entities (so no
    tag survives in the source), and the rest of the markup characters are backslash-escaped."""
    text = _short(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_{}\[\]()#+!|~-])", r"\\\1", text)


def html_table(
    title: str, header: Sequence[str], rows: Sequence[Sequence[Any]], note: str = ""
) -> str:
    shown = rows[:MAX_ROWS]
    out = [f"<div><b>{_e(title)}</b>"]
    if note:
        out.append(f"<div>{_e(note)}</div>")
    out.append(f'<table style="{_STYLE}"><thead><tr>')
    out.extend(f'<th style="{_CELL}">{_e(h)}</th>' for h in header)
    out.append("</tr></thead><tbody>")
    for row in shown:
        out.append("<tr>" + "".join(f'<td style="{_CELL}">{_e(c)}</td>' for c in row) + "</tr>")
    out.append("</tbody></table>")
    if len(rows) > len(shown):
        out.append(f"<div>… {len(rows) - len(shown)} more</div>")
    out.append("</div>")
    return "".join(out)


def md_table(
    title: str, header: Sequence[str], rows: Sequence[Sequence[Any]], note: str = ""
) -> str:
    shown = rows[:MAX_ROWS]
    out = [f"**{_md(title)}**", ""]
    if note:
        out += [_md(note), ""]
    out.append("| " + " | ".join(_md(h) for h in header) + " |")
    out.append("|" + "---|" * len(header))
    out.extend("| " + " | ".join(_md(c) for c in row) + " |" for row in shown)
    if len(rows) > len(shown):
        out += ["", f"… {len(rows) - len(shown)} more"]
    return "\n".join(out) + "\n"


# -- profile ---------------------------------------------------------------------------------


def _profile_rows(profile: Any) -> list[tuple[str, list[list[Any]], int]]:
    from shape.privacy.safe_profile import to_safe_profile

    safe = to_safe_profile(profile)
    manifest = safe.redaction_manifest.get("tables", {})
    out = []
    for tname, table in safe.tables.items():
        rows = []
        for cname, col in table.columns.items():
            info = manifest.get(tname, {}).get(cname, {})
            guarded = bool(info.get("pattern_only") or info.get("sensitive"))
            mean = "" if guarded or col.mean is None else _num(col.mean)
            nulls = "" if col.null_rate is None else f"{col.null_rate:.1%}"
            rows.append(
                [cname, col.dtype, nulls, col.cardinality, "pattern only" if guarded else mean]
            )
        out.append((tname, rows, int(getattr(table, "row_count", 0) or 0)))
    return out


def profile_html(profile: Any) -> str:
    parts = []
    for tname, rows, n in _profile_rows(profile):
        note = f"{n} rows, {len(rows)} columns" if n else f"{len(rows)} columns"
        parts.append(
            html_table(
                f"Profile: {tname}", ["column", "type", "nulls", "distinct", "mean"], rows, note
            )
        )
    return "".join(parts)


def profile_markdown(profile: Any) -> str:
    parts = []
    for tname, rows, n in _profile_rows(profile):
        note = f"{n} rows, {len(rows)} columns" if n else f"{len(rows)} columns"
        parts.append(
            md_table(
                f"Profile: {tname}", ["column", "type", "nulls", "distinct", "mean"], rows, note
            )
        )
    return "\n".join(parts)


# -- diff and drift --------------------------------------------------------------------------


def _explained_rows(report: Any) -> tuple[str, list[list[Any]]]:
    from .explain import explain

    ex = explain(report).to_dict()
    head = (
        f"{ex['counts']['changes']} change(s) in {ex['counts']['columns']} column(s)"
        if ex["counts"]["changes"]
        else "No change"
    )
    rows = [
        [c["column"], ch["kind"], ch["severity"], f"{ch['score']:g}", ch["sentence"]]
        for c in ex["columns"]
        for ch in c["changes"]
    ]
    return head, rows


_CHANGE_HEADER = ["column", "kind", "severity", "score", "what changed"]


def diff_html(report: Any) -> str:
    head, rows = _explained_rows(report)
    return html_table("Diff", _CHANGE_HEADER, rows, head)


def diff_markdown(report: Any) -> str:
    head, rows = _explained_rows(report)
    return md_table("Diff", _CHANGE_HEADER, rows, head)


def drift_html(report: Any) -> str:
    head, rows = _explained_rows(report)
    return html_table("Drift", _CHANGE_HEADER, rows, head)


def drift_markdown(report: Any) -> str:
    head, rows = _explained_rows(report)
    return md_table("Drift", _CHANGE_HEADER, rows, head)


# -- check -----------------------------------------------------------------------------------


def _check_rows(result: Any) -> tuple[str, list[list[Any]]]:
    rows = []
    for v in result.violations:
        rule = str(v.get("rule"))
        if rule in _AGGREGATE_RULES:
            exp, obs = _plain(v.get("expected")), _plain(v.get("observed"))
        else:
            exp = obs = "withheld"
        rows.append([v.get("column") if v.get("column") is not None else "(table)", rule, exp, obs])
    head = "Passed" if result.passed else f"Failed: {len(rows)} violation(s)"
    return head, rows


def _plain(value: Any) -> str:
    if isinstance(value, Mapping):
        return ", ".join(
            f"{k}={_num(v)}" for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))
        )
    return _num(value)


def check_html(result: Any) -> str:
    head, rows = _check_rows(result)
    return html_table("Check", ["column", "rule", "expected", "observed"], rows, head)


def check_markdown(result: Any) -> str:
    head, rows = _check_rows(result)
    return md_table("Check", ["column", "rule", "expected", "observed"], rows, head)
