"""A data dictionary from a profile: one entry per table and column, as JSON, Markdown or HTML.

The JSON document (``format: shape-data-dictionary``, integer ``version``) is the one source;
Markdown and HTML are rendered from it, so the three always agree and the same inputs give the
same bytes (no timestamp, no path, no random order).

A column is **classified** before anything about its values is written: ``CONFIDENTIAL`` when
the safe profile's personal-data gate fires on it (a personal-data pattern, or nearly every value
distinct), when a name or pattern proposal marks it as personal data, or when ``shape.yml``
annotates it ``classification: <label>`` at that level or higher; ``INTERNAL`` otherwise. An
annotation can only raise a column's class. A column at ``CONFIDENTIAL`` or above never gets a
numeric range, example values or top values, whatever the options.
"""

from __future__ import annotations

import html
from collections.abc import Mapping
from typing import Any

FORMAT = "shape-data-dictionary"
VERSION = 1

DEFAULT_CLASS = "INTERNAL"
WITHHOLD_FROM = "CONFIDENTIAL"  # this class and above: no values of any kind
TOP_VALUES = 5
EXAMPLES = 3
_PII_FLOOR = 0.3  # the weakest personal-data proposal (a bare "name") still classifies

_COLUMN_KEYS = (
    "name", "type", "null_rate", "distinct_estimate", "semantic", "classification",
    "numeric_range", "length_range", "format_pattern", "owner", "annotations",
)  # fmt: skip


def _classification(label: Any) -> str:
    from shape.privacy.classification import DEFAULT_TAXONOMY

    try:
        return DEFAULT_TAXONOMY.canonical(str(label))
    except ValueError:
        return WITHHOLD_FROM  # an unknown label is read as the safe side, never as public


def classify(
    column: Mapping[str, Any], row_count: int, *, pii_proposed: bool, annotated: Any = None
) -> str:
    """The class of one column (see the module docstring)."""
    from shape.privacy.classification import DEFAULT_TAXONOMY
    from shape.privacy.safe_profile import SafeConfig, pii_gate_fires

    rates: dict[str, float] = {}
    for key in ("pattern_rates", "pattern_contains_rates"):
        for fam, rate in (column.get(key) or {}).items():
            rates[fam] = max(rate, rates.get(fam, 0.0))
    labels = [DEFAULT_CLASS]
    if pii_proposed or pii_gate_fires(
        column.get("pattern"), int(column.get("cardinality") or 0), row_count, SafeConfig(), rates
    ):
        labels.append(WITHHOLD_FROM)
    if annotated is not None:
        labels.append(_classification(annotated))
    return DEFAULT_TAXONOMY.join(*labels)


def _withheld(label: str) -> bool:
    from shape.privacy.classification import DEFAULT_TAXONOMY

    return DEFAULT_TAXONOMY.at_least(label, WITHHOLD_FROM)


def _tagged(v: Any) -> tuple[str, Any] | None:
    return (v[0], v[1]) if isinstance(v, list) and len(v) == 2 else None


def _numeric_range(column: Mapping[str, Any]) -> dict[str, Any] | None:
    lo, hi = _tagged(column.get("min_value")), _tagged(column.get("max_value"))
    if lo is None or hi is None or lo[0] not in ("int", "float") or hi[0] not in ("int", "float"):
        return None
    if lo[1] is None or hi[1] is None:
        return None
    return {"min": lo[1], "max": hi[1]}


def _length_range(column: Mapping[str, Any]) -> dict[str, int] | None:
    sl = column.get("string_length")
    if not isinstance(sl, Mapping) or sl.get("min") is None or sl.get("max") is None:
        return None
    return {"min": int(sl["min"]), "max": int(sl["max"])}


def _values(column: Mapping[str, Any]) -> dict[str, Any]:
    """``top_values`` and ``examples`` of a column whose class allows them."""
    order = column.get("value_counts_ext_order") or list(column.get("value_counts_ext") or {})
    counts = column.get("value_counts_ext") or {}
    top = [{"value": str(v), "share": counts[v]} for v in order[:TOP_VALUES] if v in counts]
    picks: list[str] = []
    for tagged in (column.get("min_value"), column.get("max_value")):
        t = _tagged(tagged)
        if t is not None and t[1] is not None:
            picks.append(str(t[1]))
    picks += [t["value"] for t in top]
    seen: list[str] = []
    for p in picks:
        if p not in seen:
            seen.append(p)
    return {"top_values": top, "examples": seen[:EXAMPLES]}


def build(
    profile: Any,
    *,
    source: Any = None,
    examples: bool = False,
) -> dict[str, Any]:
    """The dictionary document of ``profile`` (a ``Profile`` or its path). ``source`` is a
    ``shape.project.Source`` supplying owners, annotations and classifications."""
    import shape
    from shape.proposals.columns import propose_pii, propose_semantics

    if isinstance(profile, str) or hasattr(profile, "__fspath__"):
        profile = shape.load(str(profile))
    pii = {p.subject for p in propose_pii(profile, min_confidence=_PII_FLOOR)}
    semantic = {p.subject: p for p in propose_semantics(profile, min_confidence=0.5)}
    tables: list[dict[str, Any]] = []
    for tname in sorted(profile.tables):
        table = profile.tables[tname]
        rows = int(table["row_count"])
        entries: list[dict[str, Any]] = []
        for cname, col in table["columns"].items():
            notes = source.annotations_of(cname, tname) if source is not None else {}
            owner = source.owner_of(cname, tname) if source is not None else None
            label = classify(
                col,
                rows,
                pii_proposed=f"{tname}.{cname}" in pii,
                annotated=notes.get("classification"),
            )
            sem = semantic.get(f"{tname}.{cname}")
            entry: dict[str, Any] = {
                "name": cname,
                "type": col["dtype"],
                "null_rate": col["null_rate"],
                "distinct_estimate": col["cardinality"],
                "semantic": (
                    {"label": sem.claim["semantic"], "confidence": round(sem.confidence, 4)}
                    if sem is not None
                    else None
                ),
                "classification": label,
                "numeric_range": None if _withheld(label) else _numeric_range(col),
                "length_range": _length_range(col),
                "format_pattern": col.get("pattern"),
                "owner": owner,
                "annotations": dict(sorted(notes.items())),
            }
            if examples and not _withheld(label):
                entry.update(_values(col))
            entries.append(entry)
        tables.append(
            {
                "name": tname,
                "row_count": rows,
                "primary_key": list(table.get("primary_key") or []),
                "columns": entries,
            }
        )
    return {
        "format": FORMAT,
        "version": VERSION,
        "profile": profile.name,
        "source": source.name if source is not None else None,
        "examples_included": bool(examples),
        "tables": tables,
    }


def check(doc: Any) -> dict[str, Any]:
    """``doc`` when it is a dictionary this Shape can render; ``ValueError`` otherwise."""
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise ValueError(f"not a {FORMAT} document")
    version = doc.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError(f"{FORMAT}: version must be an integer from 1")
    if version > VERSION:
        raise ValueError(
            f"{FORMAT} version {version} is newer than this Shape reads (version {VERSION}): "
            "upgrade Shape"
        )
    tables = doc.get("tables")
    if not isinstance(tables, list) or not all(
        isinstance(t, dict) and isinstance(t.get("columns"), list) for t in tables
    ):
        raise ValueError(f"{FORMAT}: tables must be a list of tables with columns")
    return doc


def dumps(doc: dict[str, Any]) -> str:
    import json

    return (
        json.dumps(check(doc), indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    )


# ---- rendering ---------------------------------------------------------------------------------


def _pct(rate: Any) -> str:
    return "" if rate is None else f"{rate * 100:.2f}".rstrip("0").rstrip(".") + "%"


def _num(v: Any) -> str:
    return f"{v:.10g}" if isinstance(v, float) else str(v)


def _range(r: Any) -> str:
    return "" if not r else f"{_num(r['min'])} to {_num(r['max'])}"


def _semantic(s: Any) -> str:
    return "" if not s else f"{s['label']} ({s['confidence']:g})"


def _notes(n: Mapping[str, Any]) -> str:
    return ", ".join(f"{k}: {v}" for k, v in n.items())


def _top(c: Mapping[str, Any]) -> str:
    return ", ".join(f"{t['value']} ({_pct(t['share'])})" for t in c.get("top_values", []))


def _cells(c: Mapping[str, Any], examples: bool) -> list[str]:
    row = [
        str(c["name"]), str(c["type"]), _pct(c["null_rate"]), str(c["distinct_estimate"]),
        _semantic(c["semantic"]), str(c["classification"]), _range(c["numeric_range"]),
        _range(c["length_range"]), str(c["format_pattern"] or ""), str(c["owner"] or ""),
        _notes(c["annotations"]),
    ]  # fmt: skip
    if examples:
        row += [", ".join(c.get("examples", [])), _top(c)]
    return row


_HEADERS = (
    "Column", "Type", "Null rate", "Distinct (est.)", "Semantic label", "Class",
    "Numeric range", "Length range", "Format pattern", "Owner", "Annotations",
)  # fmt: skip


def _headers(doc: Mapping[str, Any]) -> list[str]:
    return [*_HEADERS, *(["Examples", "Top values"] if doc.get("examples_included") else [])]


def _title(doc: Mapping[str, Any]) -> str:
    return f"Data dictionary: {doc.get('profile') or 'profile'}"


def _md(text: str) -> str:
    return " ".join(text.replace("|", "\\|").split())


def render_markdown(doc: dict[str, Any]) -> str:
    check(doc)
    ex = bool(doc.get("examples_included"))
    out = [f"# {_title(doc)}", ""]
    if doc.get("source"):
        out += [f"Project source: `{doc['source']}`", ""]
    for t in doc["tables"]:
        pk = ", ".join(f"`{k}`" for k in t["primary_key"])
        out += [
            f"## {t['name']}",
            "",
            f"{t['row_count']} rows" + (f"; key: {pk}" if pk else ""),
            "",
        ]
        out.append("| " + " | ".join(_headers(doc)) + " |")
        out.append("|" + "---|" * len(_headers(doc)))
        for c in t["columns"]:
            cells = _cells(c, ex)
            cells[0] = f"`{cells[0]}`"
            out.append("| " + " | ".join(_md(x) if i else x for i, x in enumerate(cells)) + " |")
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


_CSS = (
    "body{font:15px/1.5 system-ui,sans-serif;margin:2rem;color:#1b1b1b;background:#fff}"
    "table{border-collapse:collapse;margin:1rem 0 2rem}"
    "th,td{border:1px solid #c8c8c8;padding:.3rem .6rem;text-align:left;vertical-align:top}"
    "th{background:#f0f0f0}code{font-family:ui-monospace,monospace}"
    "@media (prefers-color-scheme:dark){body{color:#e6e6e6;background:#161616}"
    "th{background:#262626}th,td{border-color:#444}}"
)


def render_html(doc: dict[str, Any]) -> str:
    check(doc)
    ex = bool(doc.get("examples_included"))
    e = html.escape
    title = e(_title(doc))
    out = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        f'<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, '
        f'initial-scale=1"><title>{title}</title><style>{_CSS}</style></head>',
        "<body>",
        f"<h1>{title}</h1>",
    ]
    if doc.get("source"):
        out.append(f"<p>Project source: <code>{e(str(doc['source']))}</code></p>")
    for t in doc["tables"]:
        pk = ", ".join(t["primary_key"])
        out.append(f"<h2>{e(str(t['name']))}</h2>")
        out.append(f"<p>{t['row_count']} rows" + (f"; key: {e(pk)}" if pk else "") + "</p>")
        out.append(
            "<table><thead><tr>"
            + "".join(f"<th>{e(h)}</th>" for h in _headers(doc))
            + "</tr></thead><tbody>"
        )
        for c in t["columns"]:
            cells = [e(x) for x in _cells(c, ex)]
            cells[0] = f"<code>{cells[0]}</code>"
            out.append("<tr>" + "".join(f"<td>{x}</td>" for x in cells) + "</tr>")
        out.append("</tbody></table>")
    out += ["</body>", "</html>"]
    return "\n".join(out) + "\n"
