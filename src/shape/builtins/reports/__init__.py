"""The built-in ``shape.reports`` formats: JSON, Markdown and HTML renderers of a fidelity report.

Each takes the mapping :meth:`FidelityReport.to_dict` returns and gives deterministic bytes (no
timestamps), so a report can be committed and diffed.
"""

from __future__ import annotations

import html
import json
from collections.abc import Mapping
from typing import Any

GOOD = 85.0
FAIR = 70.0
_DASH = "-"


def _f(x: Any, digits: int = 3) -> str:
    return _DASH if x is None else f"{x:.{digits}f}"


def _tables(report: Mapping[str, Any]) -> Mapping[str, Mapping[str, Any]]:
    tables: Mapping[str, Mapping[str, Any]] = report.get("tables", {})
    return tables


class JsonReport:
    """The report as indented JSON with sorted keys."""

    name = "json"
    extension = ".json"

    def render(self, report: Mapping[str, Any]) -> bytes:
        return (json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


class MarkdownReport:
    """The report as Markdown: verdict, one table per data table, the failures last."""

    name = "md"
    extension = ".md"

    def render(self, report: Mapping[str, Any]) -> bytes:
        lines = ["# Fidelity report", ""]
        verdict = "PASS" if report.get("passed") else "FAIL"
        lines.append(f"**Overall score: {_f(report.get('overall_score'), 1)}/100 - {verdict}**")
        t = report.get("thresholds") or {}
        if t:
            lines.append("")
            lines.append(
                f"Pass marks: overall {t.get('min_overall'):g}, table {t.get('min_table'):g}"
                + (f", column {t['min_column']:g}" if t.get("min_column") is not None else "")
            )
        for name, tf in _tables(report).items():
            lines += ["", f"## {name} - {_f(tf.get('score'), 1)}/100", ""]
            lines.append(
                f"{tf.get('row_count_real', 0):,} reference rows, "
                f"{tf.get('row_count_synth', 0):,} synthetic rows"
            )
            lines += [
                "",
                "| Column | Score | Kind match | Null delta | Cardinality | KS | Chi2 | Overlap |",
                "|---|---|---|---|---|---|---|---|",
            ]
            for col, c in tf.get("columns", {}).items():
                if not c.get("present", True):
                    lines.append(f"| {col} | 0.0 | missing | - | - | - | - | - |")
                    continue
                mark = "yes" if c.get("dtype_match") else "no"
                lines.append(
                    f"| {col} | {_f(c.get('score'), 1)} | {mark} | {_f(c.get('null_rate_delta'))}"
                    f" | {_f(c.get('cardinality_ratio'))} | {_f(c.get('ks_statistic'))}"
                    f" | {_f(c.get('chi2_statistic'), 1)} | {_f(c.get('value_overlap'))} |"
                )
        extra = [f"- {x}" for x in report.get("failures", [])]
        if extra:
            lines += ["", "## Failures", "", *extra]
        if report.get("extra_tables"):
            lines += ["", "Tables only in the synthetic data: " + ", ".join(report["extra_tables"])]
        return ("\n".join(lines) + "\n").encode()


def _band(score: float) -> str:
    return "good" if score >= GOOD else "fair" if score >= FAIR else "poor"


class HtmlReport:
    """The report as one self-contained HTML page: inline styles, no scripts, no network."""

    name = "html"
    extension = ".html"

    def render(self, report: Mapping[str, Any]) -> bytes:
        esc = html.escape
        overall = float(report.get("overall_score") or 0.0)
        verdict = "PASS" if report.get("passed") else "FAIL"
        body = [
            "<h1>Fidelity report</h1>",
            f'<p class="overall {_band(overall)}">{overall:.1f} / 100 '
            f'<span class="verdict {verdict.lower()}">{verdict}</span></p>',
        ]
        rows: list[str] = []
        for name, tf in _tables(report).items():
            score = float(tf.get("score") or 0.0)
            rows.append(
                f'<tr class="table"><td colspan="9">{esc(str(name))} - '
                f'<span class="{_band(score)}">{score:.1f}/100</span> '
                f'<span class="muted">{tf.get("row_count_real", 0):,} reference rows, '
                f"{tf.get('row_count_synth', 0):,} synthetic rows</span></td></tr>"
            )
            for col, c in tf.get("columns", {}).items():
                if not c.get("present", True):
                    rows.append(
                        f'<tr><td>{esc(str(col))}</td><td class="poor">0.0</td>'
                        '<td colspan="7">missing from the synthetic data</td></tr>'
                    )
                    continue
                cs = float(c.get("score") or 0.0)
                rows.append(
                    f'<tr><td>{esc(str(col))}</td><td class="{_band(cs)}">{cs:.1f}</td>'
                    f"<td>{'yes' if c.get('dtype_match') else 'no'}</td>"
                    f"<td>{_f(c.get('null_rate_delta'))}</td>"
                    f"<td>{_f(c.get('cardinality_ratio'))}</td>"
                    f"<td>{_f(c.get('ks_statistic'))}</td>"
                    f"<td>{_f(c.get('chi2_statistic'), 1)}</td>"
                    f"<td>{_f(c.get('chi2_pvalue'))}</td>"
                    f"<td>{_f(c.get('value_overlap'))}</td></tr>"
                )
        body.append(
            "<table><thead><tr><th>Column</th><th>Score</th><th>Kind</th><th>Null &Delta;</th>"
            "<th>Cardinality</th><th>KS</th><th>Chi&sup2;</th><th>Chi&sup2; p</th>"
            "<th>Overlap</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
        )
        fails = report.get("failures", [])
        if fails:
            body.append(
                "<h2>Failures</h2><ul>"
                + "".join(f"<li>{esc(str(x))}</li>" for x in fails)
                + "</ul>"
            )
        body.append(
            '<p class="muted"><span class="good">&#9632;</span> 85 and above '
            '<span class="fair">&#9632;</span> 70 to 85 <span class="poor">&#9632;</span> below 70'
            "</p>"
        )
        page = (
            '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"<title>Fidelity report</title>\n<style>{_CSS}</style>\n</head>\n<body>\n"
            + "\n".join(body)
            + "\n</body>\n</html>\n"
        )
        return page.encode()


_CSS = (
    "body{font-family:system-ui,sans-serif;margin:24px;color:#1f2933;background:#fff}"
    "h1{font-size:20px;margin:0 0 4px}.overall{font-size:30px;font-weight:700;margin:0 0 20px}"
    ".verdict{font-size:14px;padding:2px 8px;border-radius:4px;color:#fff;vertical-align:middle}"
    ".verdict.pass{background:#2d7d46}.verdict.fail{background:#c0392b}"
    "table{border-collapse:collapse;width:100%;font-size:13px}"
    "th{background:#333;color:#fff;text-align:left;padding:6px 8px;font-size:11px}"
    "td{border-bottom:1px solid #eee;padding:5px 8px}tr.table td{background:#f5f5f5;"
    "font-weight:600}.good{color:#2d7d46}.fair{color:#b45309}.poor{color:#c0392b}"
    ".muted{color:#6b7280;font-weight:400;font-size:12px}"
    "@media (prefers-color-scheme:dark){body{background:#111;color:#e5e7eb}"
    "td{border-color:#333}tr.table td{background:#1c1c1c}}"
)
