"""``shape explain``: a plain-English narrative of a diff or a drift report.

The text is built from the report alone with fixed templates: the same input gives the same text,
and nothing calls a model or the network. Raw values never reach the narrative for a classified
column. A column is *withheld* when the caller names it (``classified``), when the safe-profile
rules mark it pattern-only or sensitive in a profile passed as ``baseline`` or ``current``, or, with
no profile to ask, when the change kind carries values (a range, a category list). A withheld
column keeps its change kinds, severities and scores, and nothing else.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

FORMAT = "shape.explain"
VERSION = 1

# Likely kinds of cause, by change kind. A kind of cause is a place to look, never a finding.
_CAUSES: dict[str, str] = {
    "dtype_change": "a schema or parsing change upstream",
    "null_rate_change": "a feed that stopped filling the field, or a join that no longer matches",
    "cardinality_change": "a change in how many distinct entities the source covers",
    "uniqueness_change": "duplicated or re-keyed records",
    "mean_shift": "a change in the population, units or currency",
    "spread_change": "a change in the population, or a clipping or rounding step",
    "distribution_shift": "a change in the population or in a business rule",
    "distribution_change": "a change in the population or in a business rule",
    "range_change": "new extremes, or a unit or scale change",
    "outlier_rate_change": "a data-entry or sensor fault, or a new kind of record",
    "new_categorical_values": "a new code list, a new product or region, or a typo at the source",
    "category_shift": "a change in the mix of the population",
    "true_rate_change": "a change in a flag's business rule",
    "pattern_change": "a format change upstream",
    "length_change": "a format or truncation change upstream",
    "hour_of_day_change": "a time zone or scheduling change",
    "day_of_week_change": "a change in when records are produced",
    "column_added": "a schema change upstream",
    "column_removed": "a schema change upstream",
    "row_count_change": "a partial load, a filter change, or a change in volume",
}

# What a kind says, as a clause after the column name; ``{b}`` and ``{c}`` are the values.
_SAYS: dict[str, str] = {
    "dtype_change": "changed type from {b} to {c}",
    "null_rate_change": "null rate moved from {b} to {c}",
    "cardinality_change": "distinct values moved from {b} to {c}",
    "uniqueness_change": "share of unique values moved from {b} to {c}",
    "mean_shift": "mean moved from {b} to {c}",
    "spread_change": "spread (standard deviation) moved from {b} to {c}",
    "outlier_rate_change": "outlier rate moved from {b} to {c}",
    "true_rate_change": "true rate moved from {b} to {c}",
    "length_change": "mean length moved from {b} to {c}",
    "distribution_change": "best-fit distribution changed from {b} to {c}",
}
_PLAIN: dict[str, str] = {
    "distribution_shift": "the distribution shifted",
    "range_change": "the range widened past the baseline's",
    "new_categorical_values": "new category values appeared",
    "category_shift": "the mix of categories shifted",
    "pattern_change": "the value pattern changed",
    "hour_of_day_change": "the hour-of-day profile changed",
    "day_of_week_change": "the day-of-week profile changed",
    "column_added": "the column is new",
    "column_removed": "the column is gone",
}
# Kinds whose baseline and current hold values (extremes, category labels, patterns): the narrative
# and the structure keep a count or a plain statement of them, never the values, for any column.
_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


def _size(score: float) -> str:
    if score < 0.1:
        return "slight"
    if score < 0.3:
        return "moderate"
    if score < 0.6:
        return "large"
    return "very large"


def _fmt(v: Any) -> str:
    if isinstance(v, bool) or v is None:
        return str(v).lower() if v is not None else "none"
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


def _plain_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


@dataclass(frozen=True)
class Explanation:
    """The narrative (``text``) and the structure under it (``to_dict()``)."""

    text: str
    data: dict[str, Any] = field(repr=False)

    def to_dict(self) -> dict[str, Any]:
        import copy

        return copy.deepcopy(self.data)

    def __str__(self) -> str:
        return self.text


def guarded_columns(*profiles: Any) -> set[str]:
    """Columns the safe-profile rules reduce to a pattern or a sensitive minimum cohort, as bare
    names and ``table.column``; empty when no profile is given."""
    from shape.privacy.safe_profile import to_safe_profile

    out: set[str] = set()
    for p in profiles:
        if p is None:
            continue
        manifest = to_safe_profile(p).redaction_manifest
        for table, cols in manifest.get("tables", {}).items():
            for name, info in cols.items():
                if info.get("pattern_only") or info.get("sensitive"):
                    out.update((name, f"{table}.{name}"))
    return out


def _matches(column: str | None, patterns: Iterable[str]) -> bool:
    if column is None:
        return False
    bare = column.rpartition(".")[2]
    return any(fnmatch.fnmatchcase(n, pat) for pat in patterns for n in (column, bare))


def _change_entry(rec: Mapping[str, Any], withheld: bool) -> dict[str, Any]:
    kind = str(rec.get("kind", ""))
    score = float(rec.get("score") or 0.0)
    out: dict[str, Any] = {
        "kind": kind,
        "severity": str(rec.get("severity", "")),
        "score": round(score, 4),
        "size": _size(score),
    }
    if withheld:
        out["sentence"] = f"a {out['size']} {kind.replace('_', ' ')} (values withheld)"
        return out
    b, c = rec.get("baseline"), rec.get("current")
    simple = (_plain_number(b) or isinstance(b, str)) and (_plain_number(c) or isinstance(c, str))
    if kind in _SAYS and simple:
        out["sentence"] = _SAYS[kind].format(b=_fmt(b), c=_fmt(c))
        out["baseline"], out["current"] = b, c
    elif kind == "new_categorical_values" and isinstance(b, list) and isinstance(c, list):
        out["new_value_count"] = len(set(map(str, c)) - set(map(str, b)))
        out["sentence"] = f"{out['new_value_count']} new category value(s) appeared"
    else:
        out["sentence"] = _PLAIN.get(kind, kind.replace("_", " "))
    return out


def _rank(entry: Mapping[str, Any]) -> tuple[int, float, str]:
    return (_SEVERITY_RANK.get(entry["severity"], 3), -entry["score"], entry["kind"])


def _columns_of_changes(
    changes: Iterable[Mapping[str, Any]], guard: set[str], classified: list[str]
) -> list[dict[str, Any]]:
    by_col: dict[str, list[Mapping[str, Any]]] = {}
    for rec in changes:
        col = rec.get("column")
        by_col.setdefault("(table)" if col is None else str(col), []).append(rec)
    columns: list[dict[str, Any]] = []
    for name, recs in by_col.items():
        withheld = _matches(name, classified) or _matches(name, guard)
        entries = sorted((_change_entry(r, withheld) for r in recs), key=_rank)
        columns.append({"column": name, "withheld": withheld, "changes": entries})
    columns.sort(key=lambda c: (_rank(c["changes"][0]), c["column"]))
    return columns


def _columns_of_drift(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The drifted columns of a ``shape drift`` report; its numbers are test statistics."""
    columns: list[dict[str, Any]] = []
    for table, res in sorted((report.get("tables") or {}).items()):
        cols = res.get("columns") or {}
        for name in sorted(res.get("drifted_columns") or []):
            info = cols.get(name) or {}
            score = float(info.get("drift_score") or 0.0)
            entry: dict[str, Any] = {
                "kind": "distribution_shift",
                "severity": "high" if score >= 0.5 else "medium",
                "score": round(score, 4),
                "size": _size(score),
                "method": info.get("method"),
                "sentence": f"the distribution drifted ({info.get('method')} test)",
            }
            if info.get("p_value") is not None:
                entry["p_value"] = info["p_value"]
            if info.get("psi") is not None:
                entry["psi"] = info["psi"]
            label = name if len(report.get("tables") or {}) == 1 else f"{table}.{name}"
            columns.append({"column": label, "withheld": False, "changes": [entry]})
    columns.sort(key=lambda c: (_rank(c["changes"][0]), c["column"]))
    return columns


def _narrative(kind: str, drifted: bool, columns: list[dict[str, Any]], causes: list[str]) -> str:
    what = "drift report" if kind == "drift" else "diff"
    n = sum(len(c["changes"]) for c in columns)
    if not n:
        return f"No change: the {what} lists nothing that moved."
    lines = [
        f"The {what} lists {n} change(s) in {len(columns)} column(s)"
        + (", the most severe first." if len(columns) > 1 else ".")
    ]
    for col in columns:
        top = col["changes"][0]
        lines.append(f"- {col['column']} ({top['severity']} severity)")
        for ch in col["changes"]:
            lines.append(f"    {ch['sentence']}; a {ch['size']} change (score {ch['score']:g}).")
    if causes:
        lines.append("Likely kinds of cause: " + "; ".join(causes) + ".")
    return "\n".join(lines)


def explain(
    report: Any,
    *,
    baseline: Any = None,
    current: Any = None,
    classified: Iterable[str] = (),
) -> Explanation:
    """Explain a diff (a ``DiffResult``, its ``to_dict()`` or a list of change records) or a
    ``shape drift`` report in plain English, deterministically.

    ``classified`` names columns (a name, ``table.column`` or a glob) to withhold values for;
    ``baseline`` and ``current`` are the profiles the report came from, whose pattern-only and
    sensitive columns are withheld too. Raises ``ValueError`` for anything else."""
    data: Any = report.to_dict() if hasattr(report, "to_dict") else report
    if isinstance(data, list):
        data = {"changes": data}
    guard = guarded_columns(baseline, current)
    patterns = [str(c) for c in classified]
    if isinstance(data, Mapping) and isinstance(data.get("changes"), list):
        kind = "diff"
        columns = _columns_of_changes(data["changes"], guard, patterns)
        drifted = bool(data.get("drifted", bool(columns)))
    elif isinstance(data, Mapping) and isinstance(data.get("tables"), Mapping) and "method" in data:
        kind = "drift"
        columns = _columns_of_drift(data)
        drifted = bool(data.get("drifted", bool(columns)))
    else:
        raise ValueError(
            "not a diff or a drift report: expected `shape diff --json` or `shape drift` output"
        )
    kinds = sorted({ch["kind"] for c in columns for ch in c["changes"]})
    causes = sorted({_CAUSES[k] for k in kinds if k in _CAUSES})
    counts = {"changes": sum(len(c["changes"]) for c in columns), "columns": len(columns)}
    structure: dict[str, Any] = {
        "format": FORMAT,
        "version": VERSION,
        "kind": kind,
        "drifted": drifted,
        "counts": {
            **counts,
            "by_severity": {
                s: sum(1 for c in columns for ch in c["changes"] if ch["severity"] == s)
                for s in ("high", "medium", "low")
            },
        },
        "columns": columns,
        "likely_causes": causes,
    }
    text = _narrative(kind, drifted, columns, causes)
    structure["text"] = text
    return Explanation(text, structure)
