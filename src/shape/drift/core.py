"""Explainable Shape drift scoring: ``compare`` is the engine's front end for Shape models and
captures (``shape.drift.engine`` holds the rules and thresholds; ``shape.diff`` is the same engine
for profiles)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .engine import Policy, diff_records, resolve_policy
from .semver import classify

# The path suffix of a change: the Shape model's own name for what moved.
_FIELD = {
    "dtype_change": "kind",
    "null_rate_change": "null_count",
    "cardinality_change": "distinct",
    "mean_shift": "mean",
    "distribution_shift": "quantiles",
    "range_change": "range",
    "spread_change": "std",
    "zero_inflation_change": "zero_inflation",
    "heaping_change": "heaping",
    "benford_change": "benford",
    "tail_change": "tail_index",
}


@dataclass(frozen=True, slots=True)
class Drift:
    """One change. ``path`` addresses it (``columns.<column>[.<field>]``, ``rows`` for the row
    count, ``joint.<dependency or association>`` for a joint change, with ``tables.<table>.`` in
    front for several tables, and ``tables.<table>`` for a table added or removed); ``column``,
    ``kind`` and ``severity`` are the engine's change record, ``class_`` and ``class_reason`` its
    change class (``breaking``, ``additive`` or ``cosmetic``; ``docs/DRIFT.md``) and ``score`` is
    its size from 0 to 1."""

    path: str
    score: float
    before: object
    after: object
    reason: str
    column: str | None = None
    kind: str = ""
    severity: str = ""
    class_: str = ""
    class_reason: str = ""
    detail: Mapping[str, Any] | None = None

    def to_change(self) -> dict[str, Any]:
        """The change as ``shape.diff`` reports it."""
        out: dict[str, Any] = {
            "column": self.column,
            "kind": self.kind,
            "baseline": self.before,
            "current": self.after,
            "severity": self.severity,
            "score": self.score,
            "class": self.class_,
            "class_reason": self.class_reason,
        }
        if self.detail:
            out["detail"] = dict(self.detail)
        return out


def _drift(table: str | None, column: str | None, record: dict[str, Any], policy: Policy) -> Drift:
    kind = record["kind"]
    found = classify(record, policy.classes_for(table, column))
    prefix = f"tables.{table}." if table is not None else ""
    if kind in ("table_added", "table_removed"):
        path = f"tables.{table}"
    elif kind in ("measure_change", "role_change"):
        category = "measures" if kind == "measure_change" else "roles"
        path = f"{prefix}{category}.{record['detail']['name']}"
    elif kind == "row_count_change":
        path = f"{prefix}rows"
    elif column is None:
        # a joint change: named by what it is about (``zip -> city``, ``a ~ b`` and its measure)
        label = str(record["column"])
        if table is not None and label.startswith(f"{table}."):
            label = label[len(table) + 1 :]
        measure = (record.get("detail") or {}).get("measure")
        path = f"{prefix}joint.{label}" + (f".{measure}" if measure else "")
    else:
        path = f"{prefix}columns.{column}"
        if kind not in ("column_added", "column_removed"):
            path += f".{_FIELD.get(kind, kind)}"
    return Drift(
        path,
        record["score"],
        record["baseline"],
        record["current"],
        kind.replace("_", " "),
        record["column"],
        kind,
        record["severity"],
        found.class_,
        found.reason,
        record.get("detail"),
    )


def compare(
    before: Any,
    after: Any,
    *,
    thresholds: Mapping[str, Any] | None = None,
    ignore_columns: Iterable[str] | None = None,
    column_thresholds: Mapping[str, Mapping[str, Any]] | None = None,
    only_columns: Iterable[str] | None = None,
    policy: Mapping[str, Any] | str | Path | None = None,
) -> list[Drift]:
    """Drift between two Shapes (v2 models, v1 captures that are migrated, profiles or window
    profiles), most severe first, by the same rules and thresholds as ``shape.diff``. Only changes
    that pass their threshold are listed, so a stable column contributes nothing."""
    resolved = resolve_policy(
        thresholds,
        ignore_columns=ignore_columns,
        column_thresholds=column_thresholds,
        only_columns=only_columns,
        policy=policy,
    )
    out = [_drift(t, c, r, resolved) for t, c, r in diff_records(before, after, resolved)]
    return sorted(out, key=lambda x: (-x.score, x.path))
