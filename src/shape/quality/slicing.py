"""Slices of a table for the scorecard: scores, representation, outcome rates and null rates.

A *slice* is each distinct value, or value combination, of the slice columns in a table that
holds them; null is the slice ``(null)``. A slice with fewer than ``min_slice_rows`` rows is never
shown alone: the small slices are pooled as ``(small slices)`` when there are at least two of
them, and a single small slice is left out (only its row count is recorded). Slice labels follow
the scorecard's safe-by-default rule: when a slice column is classified the labels are
``slice 1``, ``slice 2``, ... (largest first) unless the caller asks to show them.

Everything here is a data check on the table: no model, no sensitive attribute is inferred.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Collection, Mapping, Sequence
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from .rowlevel import _missing_mask, is_classified
from .sources import is_profile

NULL_LABEL = "(null)"
POOL_LABEL = "(small slices)"
DEFAULT_MIN_SLICE_ROWS = 30
#: The four-fifths rule: a lowest-over-highest positive rate below this is flagged. A screening
#: heuristic on the data, not a legal test.
DISPARITY_THRESHOLD = 0.8
#: A slice's null rate more than this above the table's is flagged.
NULL_RATE_MARGIN = 0.1
_MAX_MISSING = 20

Key = tuple[str | None, ...]
#: Scores a slice of a table: ``(table name, the slice's rows)`` to ``{dimension: score | None}``.
SliceScorer = Callable[[str, pa.Table], dict[str, float | None]]

_TRUE_WORDS = {"true", "yes", "y", "t", "1", "positive", "pos"}
_FALSE_WORDS = {"false", "no", "n", "f", "0", "negative", "neg"}


class SliceError(ValueError):
    """The slices cannot be built from the given columns, label or reference."""


def _norm(value: Any) -> str | None:
    return None if value is None else str(value)


def _key_text(key: Key, columns: Sequence[str]) -> str:
    if len(columns) == 1:
        return NULL_LABEL if key[0] is None else str(key[0])
    return ", ".join(
        f"{c}={NULL_LABEL if v is None else v}" for c, v in zip(columns, key, strict=True)
    )


def group_rows(table: pa.Table, columns: Sequence[str]) -> dict[Key, np.ndarray[Any, Any]]:
    """The row positions of each distinct value combination of ``columns`` (null is a value)."""
    n = table.num_rows
    idx = "__row"
    while idx in table.column_names:
        idx += "_"
    keyed = table.select(list(columns)).append_column(idx, pa.array(np.arange(n, dtype=np.int64)))
    grouped = keyed.group_by(list(columns), use_threads=False).aggregate([(idx, "list")])
    cols = [grouped.column(c).to_pylist() for c in columns]
    rows = grouped.column(f"{idx}_list").to_pylist()
    out: dict[Key, np.ndarray[Any, Any]] = {}
    for i, positions in enumerate(rows):
        key = tuple(_norm(c[i]) for c in cols)
        arr = np.sort(np.asarray(positions, dtype=np.int64))
        out[key] = np.concatenate([out[key], arr]) if key in out else arr
    return out


# -- the label ---------------------------------------------------------------------------------


def _positive_mask(
    table: pa.Table, label: str
) -> tuple[Any, np.ndarray[Any, Any], np.ndarray[Any, Any]]:
    """``(positive value, positive mask, labelled mask)`` of a boolean or two-valued column."""
    if label not in table.column_names:
        raise SliceError(f"label {label!r} is not a column of the table")
    col = table.column(label)
    labelled = ~np.asarray(pc.is_null(col).to_numpy(zero_copy_only=False), dtype=bool)
    if pa.types.is_boolean(col.type):
        pos = np.asarray(col.fill_null(False).to_numpy(zero_copy_only=False), dtype=bool)
        return True, pos, labelled
    values = sorted({v for v in col.to_pylist() if v is not None}, key=lambda v: (str(type(v)), v))
    if len(values) != 2:
        raise SliceError(
            f"label {label!r} must be boolean or have two values, not {len(values)} "
            f"({', '.join(map(str, values[:4]))}{', ...' if len(values) > 4 else ''})"
        )
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
        positive = values[1]
    else:
        words = {str(v).strip().casefold(): v for v in values}
        hits = [v for w, v in words.items() if w in _TRUE_WORDS]
        misses = [v for w, v in words.items() if w in _FALSE_WORDS]
        if len(hits) != 1 or len(misses) != 1:
            raise SliceError(
                f"label {label!r} has the values {values[0]!r} and {values[1]!r}, and Shape "
                "cannot tell which is the positive one; recode the column as boolean"
            )
        positive = hits[0]
    pos = np.array([v == positive for v in col.to_pylist()], dtype=bool) & labelled
    return positive, pos, labelled


# -- the reference -----------------------------------------------------------------------------


def _reference_shares(reference: Any, table: str, columns: Sequence[str]) -> dict[Key, float]:
    """The population share of each slice, from reference data or from a profile of it."""
    if is_profile(reference):
        from shape.drift.engine import tables_of

        if len(columns) != 1:
            raise SliceError(
                "a reference profile gives the shares of one column; slice by one column or "
                "give the reference as data"
            )
        views, _ = tables_of(reference)
        view_table = views.get(table) or (next(iter(views.values())) if len(views) == 1 else None)
        if view_table is None or columns[0] not in view_table.columns:
            raise SliceError(f"the reference profile has no column {columns[0]!r} of {table!r}")
        view = view_table.columns[columns[0]]
        if not view.categories:
            raise SliceError(
                f"the reference profile has no category shares for {columns[0]!r} "
                "(too many distinct values, or a column that is not categorical)"
            )
        null_rate = view.null_rate or 0.0
        shares: dict[Key, float] = {
            (str(k),): v * (1.0 - null_rate) for k, v in view.categories.items()
        }
        if null_rate:
            shares[(None,)] = null_rate
        return shares
    if isinstance(reference, pa.Table):
        ref_table = reference
    elif isinstance(reference, Mapping):
        ref_table = reference.get(table) or (
            next(iter(reference.values())) if len(reference) == 1 else None
        )
    else:
        ref_table = None
    if ref_table is None:
        raise SliceError(f"the reference has no table {table!r}")
    missing = [c for c in columns if c not in ref_table.column_names]
    if missing:
        raise SliceError(f"the reference table has no slice column {missing[0]!r}")
    total = ref_table.num_rows
    if total == 0:
        raise SliceError("the reference table has no rows")
    return {k: len(v) / total for k, v in group_rows(ref_table, columns).items()}


# -- one table ---------------------------------------------------------------------------------


def _r4(x: float) -> float:
    return round(float(x), 4)


def _gap(scores: Mapping[str, float | None]) -> dict[str, Any]:
    have = {k: v for k, v in scores.items() if v is not None}
    if not have:
        return {"gap": None, "worst_slice": None, "scores": {}}
    worst = min(have, key=lambda k: have[k])  # first of the lowest, in slice order
    return {
        "gap": round(max(have.values()) - min(have.values()), 2),
        "worst_slice": worst,
        "scores": have,
    }


def _null_rates(table: pa.Table, columns: Sequence[str], positions: Any) -> dict[str, float]:
    sub = table if positions is None else table.take(pa.array(positions))
    n = sub.num_rows
    out: dict[str, float] = {}
    for c in columns:
        out[c] = int(_missing_mask(sub.column(c)).sum()) / n if n else 0.0
    return out


def _slice_table(
    name: str,
    table: pa.Table,
    by: Sequence[str],
    *,
    min_slice_rows: int,
    label: str | None,
    reference: Any,
    classified: Mapping[str, Collection[str]] | None,
    show_classified: bool,
    score: SliceScorer,
) -> dict[str, Any]:
    total = table.num_rows
    groups = group_rows(table, by)
    big = {k: v for k, v in groups.items() if len(v) >= min_slice_rows}
    small = {k: v for k, v in groups.items() if len(v) < min_slice_rows}
    pool_reported = len(small) >= 2
    safe = not show_classified and any(is_classified(name, c, table, classified) for c in by)

    # order: largest first, then by label, so numbered labels do not depend on the values' order
    ordered = sorted(big, key=lambda k: (-len(big[k]), _key_text(k, by)))
    labels: dict[Key, str] = {
        k: (f"slice {i}" if safe else _key_text(k, by)) for i, k in enumerate(ordered, 1)
    }

    ref_shares = _reference_shares(reference, name, by) if reference is not None else None
    positive: Any = None
    pos_mask = labelled = None
    if label is not None:
        positive, pos_mask, labelled = _positive_mask(table, label)

    other = [c for c in table.column_names if c not in by]
    table_null = _null_rates(table, other, None)

    def entry(lbl: str, positions: np.ndarray[Any, Any], keys: Sequence[Key]) -> dict[str, Any]:
        sub = table.take(pa.array(positions))
        rates = _null_rates(sub, other, None)
        e: dict[str, Any] = {
            "slice": lbl,
            "rows": len(positions),
            "share": _r4(len(positions) / total) if total else 0.0,
            "scores": score(name, sub),
            "null_rates": {c: _r4(v) for c, v in rates.items()},
            "_rates": rates,
        }
        if ref_shares is not None:
            ref = sum(ref_shares.get(k, 0.0) for k in keys)
            e["reference_share"] = _r4(ref)
            e["ratio"] = None if ref <= 0 else _r4((len(positions) / total) / ref)
        if pos_mask is not None and labelled is not None:
            n_lab = int(labelled[positions].sum())
            e["positive_rate"] = _r4(pos_mask[positions].sum() / n_lab) if n_lab else None
            e["_rate"] = pos_mask[positions].sum() / n_lab if n_lab else None
        return e

    entries = [entry(labels[k], big[k], [k]) for k in ordered]
    if pool_reported:
        pooled = np.sort(np.concatenate(list(small.values())))
        entries.append(entry(POOL_LABEL, pooled, list(small)))
    pooled_rows = sum(len(v) for v in small.values())

    dims: dict[str, Any] = {}
    names = sorted({d for e in entries for d in e["scores"]}) if entries else []
    for d in names:
        dims[d] = _gap({e["slice"]: e["scores"].get(d) for e in entries})

    flags = []
    for e in entries:
        for c in other:
            if round(e["_rates"][c] - table_null[c], 10) > NULL_RATE_MARGIN:
                flags.append(
                    {
                        "slice": e["slice"],
                        "column": c,
                        "null_rate": _r4(e["_rates"][c]),
                        "table_null_rate": _r4(table_null[c]),
                    }
                )

    out: dict[str, Any] = {
        "rows": total,
        "slices": [],
        "small_slices": {"slices": len(small), "rows": pooled_rows, "reported": pool_reported},
        "dimensions": dims,
        "table_null_rates": {c: _r4(v) for c, v in table_null.items()},
        "null_rate_flags": flags,
    }
    if ref_shares is not None:
        data_keys = set(groups)
        absent = sorted(
            ((k, v) for k, v in ref_shares.items() if k not in data_keys and v > 0),
            key=lambda kv: (-kv[1], _key_text(kv[0], by)),
        )[:_MAX_MISSING]
        out["missing_from_data"] = [
            {
                "slice": f"slice {len(labels) + i}" if safe else _key_text(k, by),
                "reference_share": _r4(v),
            }
            for i, (k, v) in enumerate(absent, 1)
        ]
    if label is not None:
        rated = {
            e["slice"]: e["_rate"]
            for e in entries
            if e["slice"] != POOL_LABEL and e.get("_rate") is not None
        }
        lab: dict[str, Any] = {
            "column": label,
            "positive_value": positive,
            "rates": {k: _r4(v) for k, v in rated.items()},
            "threshold": DISPARITY_THRESHOLD,
            "disparity_ratio": None,
            "lowest": None,
            "highest": None,
            "flagged": False,
        }
        if rated:
            lo = min(rated, key=lambda k: rated[k])
            hi = max(rated, key=lambda k: rated[k])
            lab["lowest"], lab["highest"] = lo, hi
            if rated[hi] > 0:
                ratio = rated[lo] / rated[hi]
                lab["disparity_ratio"] = _r4(ratio)
                lab["flagged"] = bool(round(ratio, 10) < DISPARITY_THRESHOLD)
        out["label"] = lab
    for e in entries:
        e.pop("_rates", None)
        e.pop("_rate", None)
    out["slices"] = entries
    return out


# -- all tables --------------------------------------------------------------------------------


def build_slices(
    tables: Mapping[str, pa.Table],
    slice_by: Sequence[str],
    *,
    score: SliceScorer,
    min_slice_rows: int = DEFAULT_MIN_SLICE_ROWS,
    label: str | None = None,
    reference: Any = None,
    classified: Mapping[str, Collection[str]] | None = None,
    show_classified: bool = False,
    max_slice_gap: float | None = None,
) -> dict[str, Any]:
    """The ``slices`` object of a version 2 scorecard (see ``docs/FAIRNESS_AND_SKEW.md``)."""
    by = list(slice_by)
    if not by:
        raise SliceError("slice_by must name at least one column")
    if len(set(by)) != len(by):
        raise SliceError("slice_by names a column twice")
    if min_slice_rows < 1:
        raise SliceError("min_slice_rows must be 1 or more")
    if max_slice_gap is not None and (math.isnan(max_slice_gap) or max_slice_gap < 0):
        raise SliceError("max_slice_gap must be zero or more")
    holders = {n: t for n, t in tables.items() if all(c in t.column_names for c in by)}
    if not holders:
        raise SliceError(f"no table holds the slice column(s) {', '.join(by)}")
    if label is not None and not any(label in t.column_names for t in holders.values()):
        raise SliceError(f"label {label!r} is not a column of a sliced table")
    out_tables: dict[str, Any] = {}
    for name, table in holders.items():
        out_tables[name] = _slice_table(
            name,
            table,
            by,
            min_slice_rows=min_slice_rows,
            label=label,
            reference=reference,
            classified=classified,
            show_classified=show_classified,
            score=score,
        )
    doc: dict[str, Any] = {
        "by": by,
        "min_slice_rows": min_slice_rows,
        "skipped_tables": [n for n in tables if n not in holders],
        "tables": out_tables,
    }
    if max_slice_gap is not None:
        exceeded = [
            {"table": t, "dimension": d, "gap": v["gap"]}
            for t, td in out_tables.items()
            for d, v in td["dimensions"].items()
            if v["gap"] is not None and v["gap"] > max_slice_gap
        ]
        doc["max_slice_gap"] = max_slice_gap
        doc["exceeded"] = exceeded
    return doc


def slice_gaps(slices: Mapping[str, Any]) -> dict[str, float]:
    """``{"table.dimension": gap}`` of a ``slices`` object, for trends."""
    return {
        f"{t}.{d}": v["gap"]
        for t, td in slices.get("tables", {}).items()
        for d, v in td.get("dimensions", {}).items()
        if v.get("gap") is not None
    }


def gap_trend(
    current: Mapping[str, float], previous: Mapping[str, float] | None
) -> dict[str, dict[str, Any]]:
    """How each slice gap moved since the last scorecard: ``widening``, ``narrowing``,
    ``steady`` or ``no data``."""
    out: dict[str, dict[str, Any]] = {}
    for key, gap in current.items():
        prev = (previous or {}).get(key)
        if prev is None:
            out[key] = {"previous": None, "change": None, "direction": "no data"}
            continue
        change = round(gap - prev, 2)
        direction = "widening" if change > 0 else "narrowing" if change < 0 else "steady"
        out[key] = {"previous": prev, "change": change, "direction": direction}
    return out
