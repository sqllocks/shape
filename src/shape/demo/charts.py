"""The files an inference demo can write: a comparison page (``charts``) and a Power BI semantic
model (``semantic_model``).

The page is one self-contained HTML file (no script, no external asset): the fidelity score, then
for each table its columns with the real and the synthetic null rate and distinct count, and the
value shares of small categorical columns side by side.
"""

from __future__ import annotations

import html
import importlib
import re
from pathlib import Path
from typing import Any, TextIO

from shape.demo.fidelity import FidelityReport
from shape.demo.manifest import DemoManifest

_CSS = (
    "body{font-family:sans-serif;margin:24px;color:#222}"
    "table{border-collapse:collapse;margin:8px 0 24px}"
    "td,th{border:1px solid #ddd;padding:4px 8px;text-align:left;font-size:13px}"
    ".bar{display:inline-block;height:10px;vertical-align:middle}"
    ".real{background:#4682b4}.syn{background:#e9967a}"
    ".ok{color:#2a7d2a}.bad{color:#b22222}"
    ".score{font-size:32px;font-weight:bold}"
)


def _bar(share: float, css: str) -> str:
    width = max(0, min(100, round(share * 100)))
    return f'<span class="bar {css}" style="width:{width}px"></span> {share:.1%}'


_INTEGRAL_FLOAT = re.compile(r"-?\d+\.0+")


def _shares(column: Any) -> dict[str, float]:
    """A column's value shares. An integer column's values written as floats (``3.0``, as the
    profile of generated data can key them) become ``3``, so they meet the real values."""
    values = getattr(column, "enum_values", None)
    if not values:
        return {}
    if str(getattr(column, "dtype", "")) != "integer":
        return dict(values)
    shares: dict[str, float] = {}
    for key, share in values.items():
        name = str(key)
        if _INTEGRAL_FLOAT.fullmatch(name):
            name = name.split(".", 1)[0]
        shares[name] = shares.get(name, 0.0) + share
    return shares


def _categories(real: dict[str, float], synthetic: dict[str, float]) -> list[str]:
    """Every value of either side, the most frequent real values first; ties by synthetic share,
    then by name, so the page is the same bytes on every run."""
    return sorted(
        set(real) | set(synthetic),
        key=lambda k: (-real.get(k, 0.0), -synthetic.get(k, 0.0), str(k)),
    )


def render_html(real: Any, synthetic: Any, score: float, scenario: str) -> str:
    e = html.escape
    report = FidelityReport(real, synthetic)
    verdict = {(c["table"], c["column"]): c["pass"] for c in report.comparisons()}
    parts = [
        f"<h1>Shape demo — {e(scenario)}</h1>",
        f'<p class="score">{score:.1%}</p><p>of compared columns are close to the real data</p>',
    ]
    real_tables = getattr(real, "tables", {})
    syn_tables = getattr(synthetic, "tables", {})
    for tname in sorted(set(real_tables) & set(syn_tables)):
        rt, st = real_tables[tname], syn_tables[tname]
        rows = []
        details = []
        for cname in sorted(set(rt.columns) & set(st.columns)):
            rc, sc = rt.columns[cname], st.columns[cname]
            ok = verdict.get((tname, cname), True)
            rn = getattr(rc, "null_rate", None)
            sn = getattr(sc, "null_rate", None)
            rows.append(
                f"<tr><td>{e(cname)}</td><td>{e(str(rc.dtype))}</td>"
                f"<td>{'n/a' if rn is None else f'{rn:.1%}'}</td>"
                f"<td>{'n/a' if sn is None else f'{sn:.1%}'}</td>"
                f"<td>{rc.cardinality}</td><td>{sc.cardinality}</td>"
                f'<td class="{"ok" if ok else "bad"}">{"OK" if ok else "FAIL"}</td></tr>'
            )
            rs, ss = _shares(rc), _shares(sc)
            if rs and len(rs) <= 12:
                cats = _categories(rs, ss)
                body = "".join(
                    f"<tr><td>{e(str(k))}</td><td>{_bar(rs.get(k, 0.0), 'real')}</td>"
                    f"<td>{_bar(ss.get(k, 0.0), 'syn')}</td></tr>"
                    for k in cats
                )
                details.append(
                    f"<h3>{e(cname)}</h3><table><tr><th>value</th><th>real</th>"
                    f"<th>synthetic</th></tr>{body}</table>"
                )
        parts.append(
            f"<h2>{e(tname)}</h2><table><tr><th>column</th><th>type</th><th>real nulls</th>"
            f"<th>synthetic nulls</th><th>real distinct</th><th>synthetic distinct</th>"
            f"<th></th></tr>{''.join(rows)}</table>{''.join(details)}"
        )
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        f"<title>Shape demo — {e(scenario)}</title><style>{_CSS}</style></head>"
        f"<body>{''.join(parts)}</body></html>"
    )


def render_charts(
    real: Any,
    synthetic: Any,
    score: float,
    out_dir: Path,
    scenario: str,
    manifest: DemoManifest,
    out: TextIO | None = None,
) -> Path:
    """Write the comparison page into ``out_dir`` and record it in the manifest."""
    path = out_dir / f"{scenario}_charts.html"
    path.write_text(render_html(real, synthetic, score, scenario), encoding="utf-8")
    manifest.add_artifact("file", path.name, detail=str(path))
    print(f"     Comparison page written to {path}", file=out)
    return path


def write_semantic_model(
    schema: Any,
    out_dir: Path,
    scenario: str,
    manifest: DemoManifest,
    out: TextIO | None = None,
    *,
    required: bool = True,
) -> Path | None:
    """Write a Power BI ``.bim`` for ``schema`` into ``out_dir`` and record it in the manifest.

    It needs the ``shape-fabric`` plugin: asked for by name, a missing plugin is an error; asked
    for through ``all``, it is skipped with a note."""
    try:
        exporter = importlib.import_module("shape_fabric.semantic_model").SemanticModelExporter
    except ImportError as exc:
        message = (
            "the semantic model needs the shape-fabric plugin: pip install sqllocks-shape-fabric"
        )
        if required:
            raise ImportError(message) from exc
        print(f"     Skipping the semantic model: {message}", file=out)
        return None
    path = out_dir / f"{scenario}_model.bim"
    exporter().export_bim(
        schema,
        source_type="lakehouse",
        source_name="ShapeDemo",
        output_path=path,
        include_measures=True,
    )
    manifest.add_artifact("file", path.name, detail=str(path))
    print(f"     Semantic model written to {path}", file=out)
    return path
