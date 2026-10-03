"""Drift between two versions of a name in a registry, whatever form each is stored in (W7-05).

A registry stores a raw profile (a ``.shape`` artifact, with real values) or, by default and for
anything shared, its share-safe form (``shape profile safe``). ``shape diff`` compares two raw
profiles; this module gives the same change records (``kind``, ``severity``, ``score``, and the
two values) for two share-safe documents:

* every metric both safe forms hold is compared by the drift engine, with the same rules and
  thresholds as ``shape diff``;
* a metric a safe form withholds cannot be compared and is listed under ``not_measured``, never
  silently skipped: the extremes of a column (``range``: a safe form keeps winsorized bounds, not
  the minimum and maximum), the outlier rate, the spread of a column whose statistics were
  withheld, and so on.

``diff_safe`` is the comparison; ``diff_versions`` is what ``shape registry ROOT diff NAME REF1
REF2`` prints for any two versions. See ``docs/REGISTRY.md``.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shape.errors import ShapeError

_NUMERIC = ("integer", "float")
#: The metrics a safe form can withhold, by column type (``range`` and ``outlier_rate`` always).
_METRICS_NUMERIC = ("mean", "spread", "distribution_shift", "range", "outlier_rate")


class SafeDriftError(ShapeError, ValueError):
    """A document is not a share-safe profile."""


@dataclass
class _SafeProfile:
    """A safe profile document as the drift engine reads a profile: ``tables`` and
    ``is_dataset``, each column in the field names of a full profile (a withheld field is
    absent)."""

    tables: dict[str, dict[str, Any]]
    is_dataset: bool


def is_safe_profile(doc: Any) -> bool:
    """True for the parsed JSON of a share-safe profile."""
    return isinstance(doc, Mapping) and "redaction_manifest" in doc and "tables" in doc


def _profile_column(col: Mapping[str, Any], rows: int) -> dict[str, Any]:
    null_rate = col.get("null_rate")
    null_count = round(float(null_rate) * rows) if isinstance(null_rate, (int, float)) else 0
    length = col.get("string_length") or col.get("length_dist")
    out: dict[str, Any] = {
        "name": col.get("name"),
        "dtype": col["dtype"],
        "null_rate": null_rate,
        "null_count": null_count,
        "cardinality": col.get("cardinality"),
        "mean": col.get("mean"),
        "std": col.get("std"),
        "quantiles": col.get("quantiles"),
        "pattern": col.get("pattern"),
        "enum_values": col.get("categorical_weights"),
        "string_length": length if isinstance(length, Mapping) else None,
        "distribution": col.get("distribution"),
        "hour_histogram": col.get("hour_histogram"),
        "dow_histogram": col.get("dow_histogram"),
    }
    return out


def safe_profile(doc: Mapping[str, Any]) -> _SafeProfile:
    """``doc`` (a share-safe profile) as something the drift engine compares."""
    tables = doc.get("tables")
    if not isinstance(tables, Mapping) or not tables:
        raise SafeDriftError("not a share-safe profile: it has no tables")
    out: dict[str, dict[str, Any]] = {}
    for tname, table in tables.items():
        rows = int(table.get("row_count") or 0)
        out[str(tname)] = {
            "name": str(tname),
            "row_count": rows,
            "columns": {
                str(c): _profile_column(col, rows)
                for c, col in (table.get("columns") or {}).items()
            },
        }
    return _SafeProfile(out, len(out) > 1)


def _missing(col: Mapping[str, Any], *keys: str) -> bool:
    return any(col.get(k) is None for k in keys)


def _withheld_metrics(a: Mapping[str, Any], b: Mapping[str, Any]) -> list[tuple[str, str]]:
    """``(metric, why)`` for each metric of the column that one of the two safe columns does not
    hold, so that it cannot be compared."""
    out: list[tuple[str, str]] = []
    dtype = a.get("dtype")
    if dtype != b.get("dtype"):
        return out  # a type change is reported as such
    for metric, keys in (("null_rate", ("null_rate",)), ("cardinality", ("cardinality",))):
        if _missing(a, *keys) or _missing(b, *keys):
            out.append((metric, "a safe form withholds it"))
    if dtype in _NUMERIC:
        # a safe form keeps winsorized bounds from the quantiles, never the extremes themselves
        out.append(("range", "a safe form keeps bounds, not the minimum and maximum"))
        out.append(("outlier_rate", "a safe form does not hold the outlier rate"))
        for metric, key in (
            ("mean", "mean"),
            ("spread", "std"),
            ("distribution_shift", "quantiles"),
        ):
            if _missing(a, key) or _missing(b, key):
                out.append((metric, "a safe form withholds it for this column"))
    elif dtype == "datetime":
        if _missing(a, "hour_histogram") or _missing(b, "hour_histogram"):
            out.append(("hour_of_day", "a safe form withholds it for this column"))
        out.append(("day_of_week", "a safe form holds no first and last date to size the span"))
    elif dtype == "string":
        if _missing(a, "string_length") or _missing(b, "string_length"):
            out.append(("length", "a safe form withholds it for this column"))
    if (a.get("categorical_weights") is None) != (b.get("categorical_weights") is None):
        out.append(("categories", "one safe form withholds the category weights"))
    return out


def not_measured(
    first: Mapping[str, Any], second: Mapping[str, Any], policy: Any = None
) -> list[dict[str, Any]]:
    """The metrics that cannot be compared between two safe documents, for every column both
    hold: ``{"table", "column", "metric", "reason"}`` (``table`` is ``None`` for one table).
    Columns the ``policy`` ignores are left out, as their changes are."""
    a_tables, b_tables = first["tables"], second["tables"]
    dataset = len(a_tables) > 1 or len(b_tables) > 1
    out: list[dict[str, Any]] = []
    for tname in sorted(set(a_tables) & set(b_tables)):
        a_cols = a_tables[tname].get("columns") or {}
        b_cols = b_tables[tname].get("columns") or {}
        for cname in sorted(set(a_cols) & set(b_cols)):
            scope = tname if dataset else None
            if policy is not None and policy.skips(scope, cname):
                continue
            for metric, why in _withheld_metrics(a_cols[cname], b_cols[cname]):
                out.append(
                    {
                        "table": scope,
                        "column": f"{tname}.{cname}" if dataset else cname,
                        "metric": metric,
                        "reason": why,
                    }
                )
    return out


def diff_safe(
    first: Mapping[str, Any],
    second: Mapping[str, Any],
    *,
    thresholds: dict[str, Any] | None = None,
    ignore_columns: list[str] | None = None,
    column_thresholds: dict[str, dict[str, Any]] | None = None,
    only_columns: list[str] | None = None,
    policy: dict[str, Any] | str | Path | None = None,
) -> dict[str, Any]:
    """The drift between two share-safe profile documents: ``{"drifted", "changes",
    "not_measured"}``. ``changes`` are the records of ``shape diff`` (``kind``, ``severity``,
    ``score``) for every metric both documents hold; ``not_measured`` lists the ones a safe form
    withholds. The options are those of ``shape diff``."""
    from shape.drift.engine import diff_tables, resolve_policy

    for doc in (first, second):
        if not is_safe_profile(doc):
            raise SafeDriftError("not a share-safe profile")
    resolved = resolve_policy(
        thresholds,
        ignore_columns=ignore_columns,
        column_thresholds=column_thresholds,
        only_columns=only_columns,
        policy=policy,
    )
    changes = diff_tables(safe_profile(first), safe_profile(second), resolved)
    return {
        "drifted": bool(changes),
        "changes": changes,
        "not_measured": not_measured(first, second, resolved),
    }


# ---- two versions of a name ------------------------------------------------------------------


def _changed(a: Any, b: Any, prefix: str = "") -> dict[str, dict[str, Any]]:
    """The paths at which two JSON documents differ (objects are walked, lists compared whole)."""
    if isinstance(a, dict) and isinstance(b, dict):
        out: dict[str, dict[str, Any]] = {}
        for key in sorted(set(a) | set(b)):
            out.update(_changed(a.get(key), b.get(key), f"{prefix}.{key}" if prefix else key))
        return out
    return {} if a == b else {prefix or "<root>": {"from": a, "to": b}}


def _drift_raw(first: bytes, second: bytes) -> dict[str, Any]:
    """``shape diff`` of two raw profile artifacts."""
    import shape

    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for i, blob in enumerate((first, second)):
            path = Path(tmp) / f"{i}.shape"
            path.write_bytes(blob)
            paths.append(path)
        result: dict[str, Any] = shape.diff(shape.load(paths[0]), shape.load(paths[1])).to_dict()
        return result


def diff_versions(registry: Any, name: str, ref1: str, ref2: str) -> dict[str, Any]:
    """What ``shape registry ROOT diff NAME REF1 REF2`` prints. Two raw profiles give ``drift``
    (``shape diff``); two share-safe profiles give ``drift`` with ``not_measured`` (``diff_safe``)
    and ``changed`` (the paths that differ); two other documents give ``changed`` only."""
    from shape.registry.local import is_raw_profile

    id1, id2 = registry.resolve(name, ref1), registry.resolve(name, ref2)
    out: dict[str, Any] = {"name": name, "from": id1, "to": id2, "same": id1 == id2}
    if id1 == id2:
        out["changed"] = {}
        return out
    first, second = registry.checkout(name, id1), registry.checkout(name, id2)
    if is_raw_profile(first) and is_raw_profile(second) and first[:2] == b"PK":
        out["drift"] = _drift_raw(first, second)
        return out
    try:
        docs = [json.loads(x) for x in (first, second)]
    except ValueError:
        docs = []
    if len(docs) == 2 and all(isinstance(d, dict) for d in docs):
        out["changed"] = _changed(docs[0], docs[1])
        if all(is_safe_profile(d) for d in docs):
            out["drift"] = diff_safe(docs[0], docs[1])
    else:
        out["changed"] = None  # binary or text: only the content ids can be compared
    return out
