"""Self-contained HTML report: inline CSS and SVG only, no scripts and no external assets."""

from __future__ import annotations

import html
from typing import TYPE_CHECKING, Any

from shape.profile.univariate import describe

if TYPE_CHECKING:
    from shape.profile.reference.profile import Profile

_CSS = """
:root{--fg:#1f2933;--muted:#616e7c;--line:#d9e2ec;--bg:#fff;--soft:#f5f7fa;--accent:#2f6fed;
--warn:#b44d12;--ok:#1a7f37;--bad:#c62828}
@media (prefers-color-scheme:dark){:root{--fg:#e4e7eb;--muted:#9aa5b1;--line:#3e4c59;
--bg:#161b22;--soft:#1f2630;--accent:#6ea0ff;--warn:#f0a35e;--ok:#56d364;--bad:#ff7b72}}
body{font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif;color:var(--fg);
background:var(--bg);margin:0;padding:24px 16px}
main{max-width:1100px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px}h2{font-size:18px;margin:32px 0 8px}
h3{font-size:15px;margin:0 0 6px}
.meta{color:var(--muted);margin:0 0 16px}
.tablewrap{overflow-x:auto;border:1px solid var(--line);border-radius:6px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:6px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}
th{background:var(--soft);font-weight:600}
td.num{text-align:right;font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}
.badge{display:inline-block;padding:0 6px;border-radius:9px;background:var(--soft);
border:1px solid var(--line);font-size:11px;margin-right:4px}
.badge.key{border-color:var(--accent);color:var(--accent)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px;
margin-top:12px}
.card{border:1px solid var(--line);border-radius:6px;padding:10px 12px;background:var(--bg)}
.card p{margin:2px 0;color:var(--muted);font-size:12px}
svg{display:block;width:100%;height:auto;margin-top:6px}
.bar{fill:var(--accent)}.axis{stroke:var(--line)}.lbl{fill:var(--muted);font-size:9px}
.warn{color:var(--warn)}
.status{display:inline-block;padding:0 8px;border-radius:9px;font-size:12px;font-weight:600;
border:1px solid var(--line);background:var(--soft)}
.status.pass{color:var(--ok);border-color:var(--ok)}
.status.fail{color:var(--bad);border-color:var(--bad)}
.status.not_run{color:var(--muted)}
"""


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _fmt(value: Any) -> str:
    if value is None:
        return "–"
    if isinstance(value, float):
        return f"{value:.6g}"
    return _e(value)


def escape(value: Any) -> str:
    """``value`` as HTML-safe text (``None`` is empty)."""
    return _e(value)


def format_value(value: Any) -> str:
    """A cell value as HTML-safe text: ``–`` for ``None``, six significant digits for a float."""
    return _fmt(value)


def document(title: str, body: str) -> str:
    """``body`` (HTML) in the self-contained page every Shape report uses: the shared inline
    styles, no script, no external asset."""
    return (
        "<!doctype html>"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_e(title)}</title><style>{_CSS}</style></head><body><main>{body}</main>"
        "</body></html>"
    )


def _plain(tagged: Any) -> Any:
    return tagged[1] if isinstance(tagged, list) and len(tagged) == 2 else None


def _bars(labels: list[str], values: list[float], width: int = 300, height: int = 70) -> str:
    """Inline SVG bar chart of proportions."""
    if not values:
        return ""
    top = max(values) or 1.0
    n = len(values)
    gap = 2
    bw = max((width - gap * (n - 1)) / n, 1.0)
    parts = [f'<svg viewBox="0 0 {width} {height + 14}" role="img">']
    parts.append(f'<line class="axis" x1="0" y1="{height}" x2="{width}" y2="{height}"/>')
    for i, v in enumerate(values):
        h = max(v / top * (height - 4), 0.0)
        x = i * (bw + gap)
        parts.append(
            f'<rect class="bar" x="{x:.1f}" y="{height - h:.1f}" width="{bw:.1f}" '
            f'height="{h:.1f}"><title>{_e(labels[i])}: {v:.4g}</title></rect>'
        )
    step = max(n // 6, 1)
    for i in range(0, n, step):
        x = i * (bw + gap)
        parts.append(f'<text class="lbl" x="{x:.1f}" y="{height + 11}">{_e(labels[i][:8])}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _quantile_strip(q: dict[str, Any], lo: float, hi: float, width: int = 300) -> str:
    """Inline SVG box-plot style strip from the stored quantiles."""
    span = hi - lo
    if not span or span != span:
        return ""

    def xp(v: float) -> float:
        return (v - lo) / span * (width - 8) + 4

    try:
        p = {k: float(v) for k, v in q.items() if v is not None}
        whisk = (xp(p["p1"]), xp(p["p99"]))
        box = (xp(p["p25"]), xp(p["p75"]))
        med = xp(p["p50"])
    except (KeyError, TypeError, ValueError):
        return ""
    return (
        f'<svg viewBox="0 0 {width} 34" role="img">'
        f'<line class="axis" x1="{whisk[0]:.1f}" y1="14" x2="{whisk[1]:.1f}" y2="14"/>'
        f'<rect class="bar" opacity="0.35" x="{box[0]:.1f}" y="6" '
        f'width="{max(box[1] - box[0], 1):.1f}" height="16"/>'
        f'<line stroke="currentColor" x1="{med:.1f}" y1="4" x2="{med:.1f}" y2="24"/>'
        f'<text class="lbl" x="4" y="32">{_fmt(lo)}</text>'
        f'<text class="lbl" text-anchor="end" x="{width - 4}" y="32">{_fmt(hi)}</text></svg>'
    )


def _pct(rate: float | None) -> str:
    """A rate as a percentage; ``n/a`` when it is unknown (no rows were read)."""
    return "n/a" if rate is None else f"{rate * 100:.2f}%"


def _card(name: str, col: dict[str, Any]) -> str:
    facts = [
        f"{_e(col['dtype'])}",
        f"nulls {_pct(col['null_rate'])}",
        f"{col['cardinality']:,} distinct",
    ]
    if col.get("pattern"):
        facts.append(f"pattern: {_e(col['pattern'])}")
    if col.get("distribution"):
        fit = col.get("fit_score")
        facts.append(
            f"fit: {_e(col['distribution'])}" + (f" (score {fit:.3f})" if fit is not None else "")
        )
    facts.extend(_e(line) for line in describe(col))
    body = [f"<h3>{_e(name)}</h3>", "<p>" + " · ".join(facts) + "</p>"]
    if col.get("quantiles"):
        lo, hi = _plain(col["min_value"]), _plain(col["max_value"])
        if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
            body.append(_quantile_strip(col["quantiles"], float(lo), float(hi)))
    enum = col.get("enum_values") or col.get("value_counts_ext")
    if enum:
        items = list(enum.items())[:12]
        body.append(_bars([k for k, _ in items], [float(v) for _, v in items]))
        if len(enum) > 12:
            body.append(f"<p>top 12 of {len(enum)} values</p>")
    if col.get("hour_histogram"):
        h = col["hour_histogram"]
        body.append("<p>by hour</p>" + _bars([str(i) for i in range(len(h))], h))
    if col.get("dow_histogram"):
        d = col["dow_histogram"]
        body.append("<p>by weekday</p>" + _bars(["M", "T", "W", "T", "F", "S", "S"], d))
    return '<div class="card">' + "".join(body) + "</div>"


def _row(name: str, col: dict[str, Any]) -> str:
    badges = ""
    if col["is_primary_key"]:
        badges += '<span class="badge key">PK</span>'
    if col["is_foreign_key"]:
        badges += f'<span class="badge key">FK → {_e(col["fk_ref_table"])}</span>'
    if col["is_unique"]:
        badges += '<span class="badge">unique</span>'
    cells = [
        f"<td>{_e(name)}</td>",
        f"<td>{_e(col['dtype'])}</td>",
        f'<td class="num">{_pct(col["null_rate"])}</td>',
        f'<td class="num">{col["cardinality"]:,}</td>',
        f"<td>{badges}</td>",
        f"<td>{_e(col.get('pattern') or '')}</td>",
        f"<td>{_e(col.get('distribution') or '')}</td>",
        f'<td class="num">{_fmt(_plain(col["min_value"]))}</td>',
        f'<td class="num">{_fmt(_plain(col["max_value"]))}</td>',
        f'<td class="num">{_fmt(col["mean"])}</td>',
        f'<td class="num">{_fmt(col["std"])}</td>',
    ]
    return "<tr>" + "".join(cells) + "</tr>"


_HEADERS = (
    "column",
    "type",
    "null",
    "distinct",
    "keys",
    "pattern",
    "distribution",
    "min",
    "max",
    "mean",
    "std",
)


def _table_section(table: dict[str, Any], heading: str) -> str:
    cols = table["columns"]
    pk = ", ".join(table["primary_key"]) or "none detected"
    out = [
        f"<h2>{_e(heading)}</h2>",
        f'<p class="meta">{table["row_count"]:,} rows · {len(cols)} columns · '
        f"primary key: {_e(pk)}</p>",
        '<div class="tablewrap"><table><thead><tr>',
        "".join(f"<th>{h}</th>" for h in _HEADERS),
        "</tr></thead><tbody>",
        "".join(_row(n, c) for n, c in cols.items()),
        "</tbody></table></div>",
        '<div class="cards">',
        "".join(_card(n, c) for n, c in cols.items()),
        "</div>",
    ]
    return "".join(out)


def render_html(profile: Profile) -> str:
    """Render ``profile`` as one self-contained HTML document."""
    title = f"Shape profile: {profile.name}"
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{_e(title)}</title><style>{_CSS}</style></head><body><main>",
        f"<h1>{_e(title)}</h1>",
    ]
    tables = profile.tables
    total = sum(t["row_count"] for t in tables.values())
    parts.append(f'<p class="meta">{len(tables)} table(s) · {total:,} rows profiled</p>')
    for tname, table in tables.items():
        parts.append(_table_section(table, tname))
    if profile.is_dataset:
        rels = profile.to_dict()["relationships"]
        parts.append("<h2>Relationships</h2>")
        if rels:
            rows = "".join(
                f"<tr><td>{_e(r['child'])}.{_e(', '.join(r['child_columns']))}</td>"
                f"<td>→</td><td>{_e(r['parent'])}.{_e(', '.join(r['parent_columns']))}</td>"
                f"<td>{_e(r['type'])}</td></tr>"
                for r in rels
            )
            parts.append(
                '<div class="tablewrap"><table><thead><tr><th>child</th><th></th><th>parent</th>'
                f"<th>type</th></tr></thead><tbody>{rows}</tbody></table></div>"
            )
        else:
            parts.append('<p class="meta">No foreign keys detected.</p>')
    parts.append("</main></body></html>")
    return "".join(parts)
