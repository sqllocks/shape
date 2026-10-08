"""The parity checks: every comparison between side A (the reference, usually production) and
side B (the environment under test), as a flat list of :class:`Check` records.

A check is ``pass`` or ``fail``; one the inputs cannot support is ``not_measured`` and never a
pass. Statistics are compared with the drift engine's rules and the ``shape.drift`` thresholds of
the source, so "the same shape" means what ``shape diff`` means.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from shape.drift.engine import (
    Policy,
    View,
    diff_column_views,
)

from .sides import ColumnSide, Side, TableSide

CATEGORIES = (
    "tables",
    "columns",
    "types",
    "keys",
    "relationships",
    "nulls",
    "distributions",
    "row_counts",
)
PASS, FAIL, NOT_MEASURED = "pass", "fail", "not_measured"
OTHER = "__OTHER__"  # the bucket a safe profile folds small categories into
DEFAULT_ROW_TOLERANCE = 0.1
_MIN_ID_RANGE = 20
_DENSE_IDS = 0.3

# Drift kinds that belong to the other categories, or that say nothing about shape.
# ``distribution_change`` is the name of the fitted family flipping between two samples of one
# distribution; the size of a real change is ``distribution_shift``'s.
_NOT_DISTRIBUTION = {
    "dtype_change",
    "null_rate_change",
    "row_count_change",
    "distribution_change",
}
# Counts of distinct values follow the size of the table, not its shape.
_SIZE_DRIVEN = {"cardinality_change", "uniqueness_change"}
# Between environments of different size, a larger sample sees rarer values and wider ranges.
_SCALED_NOISE = {"new_categorical_values", "range_change"}


@dataclass(slots=True)
class Check:
    category: str
    status: str
    table: str | None = None
    column: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    owner: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "category": self.category,
            "status": self.status,
            "table": self.table,
            "column": self.column,
            "details": self.details,
        }
        if self.owner:
            out["owner"] = self.owner
        return out


Owner = Callable[[str, str | None], str | None]


def _no_owner(table: str, column: str | None) -> str | None:
    return None


def _hash_label(key: str) -> str:
    # the labelling of ``shape profile safe`` for categories it may not show (a test pins it)
    return hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:12]


def _status(ok: bool) -> str:
    return PASS if ok else FAIL


def _pair_tables(a: Side, b: Side) -> tuple[dict[str, str], list[str], list[str]]:
    """``(pairs, only_a, only_b)``: a one-table side is compared with a one-table side whatever
    the two are called (file names differ between environments); otherwise tables pair by name."""
    if a.single_table and b.single_table and len(a.tables) == 1 and len(b.tables) == 1:
        (ta,) = a.tables
        (tb,) = b.tables
        return {ta: tb}, [], []
    both = [t for t in a.tables if t in b.tables]
    return (
        {t: t for t in both},
        [t for t in a.tables if t not in b.tables],
        [t for t in b.tables if t not in a.tables],
    )


def _relationship_key(rel: dict[str, Any]) -> tuple[str, tuple[str, ...], str]:
    return (
        str(rel.get("child")),
        tuple(str(c) for c in rel.get("child_columns") or ()),
        str(rel.get("parent")),
    )


def _hashed(cats: dict[str, float]) -> bool:
    keys = [k for k in cats if k != OTHER]
    return bool(keys) and all(len(k) == 12 and _is_hex(k) for k in keys)


def _align(a: View, b: View) -> None:
    """Make the categories of two views comparable when exactly one comes from a safe profile:
    its odd categories are hashed (so the other side's are, too), and its small ones are folded
    into ``__OTHER__`` (so the other side's are, too)."""
    ca, cb = a.categories, b.categories
    if not ca or not cb:
        return
    if _hashed(ca) != _hashed(cb):
        raw = b if _hashed(ca) else a
        assert raw.categories is not None
        hashed: dict[str, float] = {}
        for key, share in raw.categories.items():
            label = key if key == OTHER else _hash_label(key)
            hashed[label] = hashed.get(label, 0.0) + share
        raw.categories = hashed
    ca, cb = a.categories, b.categories
    assert ca is not None and cb is not None
    if (OTHER in ca) != (OTHER in cb):
        safe, full = (a, b) if OTHER in ca else (b, a)
        assert safe.categories is not None and full.categories is not None
        folded: dict[str, float] = {}
        for key, share in full.categories.items():
            label = key if key in safe.categories else OTHER
            folded[label] = folded.get(label, 0.0) + share
        full.categories = folded


def _is_hex(text: str) -> bool:
    return all(c in "0123456789abcdef" for c in text)


def _evidence(view: View) -> bool:
    """True when the view holds something a distribution comparison can read."""
    return bool(
        view.quantiles
        or view.categories is not None
        or view.hour
        or view.dow
        or (view.mean is not None and view.std is not None)
        or view.pattern is not None
        or view.length_mean is not None
    )


def _scales_with_size(col: ColumnSide, table: TableSide, side: Side) -> bool:
    """A key, a reference to one, or an integer that runs up to the size of some table: its
    values grow with the number of rows, whatever the shape."""
    view = col.view
    if col.key or view.key_like or view.sequential:
        return True
    if view.dtype != "integer" or view.max is None or view.min is None or view.min < 0:
        return False
    # a dense run of ids: nearly every value up to the maximum occurs, and the maximum is about
    # the size of a table (a status code or a percentage never is)
    if view.max < _MIN_ID_RANGE or (view.cardinality or 0) < _DENSE_IDS * view.max:
        return False
    rows = [t.row_count for t in side.tables.values() if t.row_count > 0]
    return any(0.5 * n <= view.max <= 1.5 * n for n in rows)


def compare(
    a: Side,
    b: Side,
    *,
    policy: Policy,
    row_tolerance: float = DEFAULT_ROW_TOLERANCE,
    scaled: bool = False,
    tables: Iterable[str] | None = None,
    owner: Owner = _no_owner,
) -> list[Check]:
    """Every check between ``a`` and ``b``. ``tables`` limits the comparison to those names (as
    side A calls them)."""
    if row_tolerance < 0 or math.isnan(row_tolerance):
        raise ValueError("the row tolerance must be a number of 0 or more")
    pairs, only_a, only_b = _pair_tables(a, b)
    selected = None if tables is None else set(tables)
    if selected is not None:
        unknown = sorted(selected - set(a.tables) - set(b.tables))
        if unknown:
            raise ValueError(f"no such table on either side: {', '.join(unknown)}")
        pairs = {t: u for t, u in pairs.items() if t in selected}
        only_a = [t for t in only_a if t in selected]
        only_b = [t for t in only_b if t in selected]
    checks: list[Check] = []
    for t in only_a:
        checks.append(Check("tables", FAIL, t, details={"reason": "missing on B"}))
    for t in only_b:
        checks.append(Check("tables", FAIL, t, details={"reason": "only on B"}))
    for t in pairs:
        checks.append(Check("tables", PASS, t))
    for ta, tb in pairs.items():
        checks.extend(_column_checks(a, b, ta, tb, policy, scaled, owner))
    checks.extend(_key_checks(a, b, pairs, owner))
    checks.extend(_relationship_checks(a, b, selected, owner))
    checks.extend(_row_count_checks(a, b, pairs, row_tolerance, scaled))
    order = {c: i for i, c in enumerate(CATEGORIES)}
    checks.sort(key=lambda c: order[c.category])  # stable: the order within a category is kept
    return checks


def _column_checks(
    a: Side, b: Side, ta: str, tb: str, policy: Policy, scaled: bool, owner: Owner
) -> list[Check]:
    A, B = a.tables[ta], b.tables[tb]
    out: list[Check] = []
    names = list(A.columns) + [c for c in B.columns if c not in A.columns]
    for c in names:
        if policy.skips(ta, c):
            continue
        own = owner(ta, c)
        if c not in B.columns:
            out.append(Check("columns", FAIL, ta, c, {"reason": "missing on B"}, own))
            continue
        if c not in A.columns:
            out.append(Check("columns", FAIL, ta, c, {"reason": "only on B"}, own))
            continue
        out.append(Check("columns", PASS, ta, c, owner=own))
        ca, cb = A.columns[c], B.columns[c]
        same = ca.dtype == cb.dtype
        out.append(
            Check(
                "types",
                _status(same),
                ta,
                c,
                {"a": ca.dtype, "b": cb.dtype} if not same else {"type": ca.dtype},
                own,
            )
        )
        th = policy.for_column(ta, c)
        out.append(_null_check(ta, c, ca, cb, th, own))
        out.append(_distribution_check(ta, c, ca, cb, A, B, a, b, th, scaled, own))
    return out


def _null_check(
    table: str, column: str, ca: ColumnSide, cb: ColumnSide, th: dict[str, Any], own: str | None
) -> Check:
    ra, rb = ca.view.null_rate, cb.view.null_rate
    if ra is None or rb is None:
        return Check(
            "nulls",
            NOT_MEASURED,
            table,
            column,
            {"reason": "a side has no null rate for this column"},
            own,
        )
    diff = abs(rb - ra)
    details = {"a": ra, "b": rb, "difference": round(diff, 6), "threshold": th["null_rate"]}
    return Check("nulls", _status(diff <= th["null_rate"]), table, column, details, own)


def _distribution_check(
    table: str,
    column: str,
    ca: ColumnSide,
    cb: ColumnSide,
    ta: TableSide,
    tb: TableSide,
    a: Side,
    b: Side,
    th: dict[str, Any],
    scaled: bool,
    own: str | None,
) -> Check:
    def nm(reason: str) -> Check:
        return Check("distributions", NOT_MEASURED, table, column, {"reason": reason}, own)

    if ca.dtype != cb.dtype:
        return nm("the types differ")
    id_like = _scales_with_size(ca, ta, a) or _scales_with_size(cb, tb, b)
    if scaled and id_like:
        return nm("a key or reference column: its values grow with the table")
    if not _evidence(ca.view):
        return nm("side A holds no distribution for this column")
    if not _evidence(cb.view):
        return nm("side B holds no distribution for this column")
    va, vb = _copy(ca.view), _copy(cb.view)
    _align(va, vb)
    ignored = set(_NOT_DISTRIBUTION)
    if scaled:
        ignored |= _SIZE_DRIVEN | _SCALED_NOISE
    if id_like:
        ignored.add("new_categorical_values")  # a new load brings new ids
    changes = [
        c
        for c in diff_column_views(column, va, vb, th)
        if c["kind"] not in ignored
        # unseen values count once they carry more than the category threshold of the rows: a
        # rare value showing up in a fresh sample is not another shape
        and not (c["kind"] == "new_categorical_values" and c["score"] <= th["category_tvd"])
    ]
    if not changes:
        return Check("distributions", PASS, table, column, owner=own)
    return Check(
        "distributions",
        FAIL,
        table,
        column,
        {"changes": [{k: c[k] for k in ("kind", "baseline", "current", "score")} for c in changes]},
        own,
    )


def _copy(view: View) -> View:
    from dataclasses import replace

    return replace(view, categories=dict(view.categories) if view.categories else view.categories)


def _key_checks(a: Side, b: Side, pairs: dict[str, str], owner: Owner) -> list[Check]:
    out: list[Check] = []
    for ta, tb in pairs.items():
        pa, pb = a.tables[ta].primary_key, b.tables[tb].primary_key
        if pa is None or not pa:
            continue  # nothing detected on A, so nothing to require of B
        if pb is None:
            out.append(
                Check(
                    "keys",
                    NOT_MEASURED,
                    ta,
                    details={"reason": "side B records no keys", "a": pa},
                )
            )
            continue
        ok = sorted(pa) == sorted(pb)
        out.append(
            Check(
                "keys",
                _status(ok),
                ta,
                pa[0] if len(pa) == 1 else None,
                {"a": pa, "b": pb},
                owner(ta, pa[0]) if len(pa) == 1 else None,
            )
        )
    return out


def _relationship_checks(a: Side, b: Side, selected: set[str] | None, owner: Owner) -> list[Check]:
    if a.relationships is None:
        return []
    if b.relationships is None:
        rels = [r for r in a.relationships if selected is None or r.get("child") in selected]
        return [
            Check(
                "relationships",
                NOT_MEASURED,
                str(r.get("child")),
                (r.get("child_columns") or [None])[0],
                {"reason": "side B records no relationships", "parent": r.get("parent")},
            )
            for r in rels
        ]
    present = {_relationship_key(r) for r in b.relationships}
    out: list[Check] = []
    for r in a.relationships:
        child = str(r.get("child"))
        if selected is not None and child not in selected:
            continue
        cols = [str(c) for c in r.get("child_columns") or ()]
        found = _relationship_key(r) in present
        column = cols[0] if len(cols) == 1 else None
        details: dict[str, Any] = {
            "parent": r.get("parent"),
            "child_columns": cols,
            "relationship": r.get("name"),
        }
        if not found:
            details["reason"] = "not detected on B"
        out.append(
            Check(
                "relationships",
                _status(found),
                child,
                column,
                details,
                owner(child, column) if column else None,
            )
        )
    return out


def _row_count_checks(
    a: Side, b: Side, pairs: dict[str, str], tolerance: float, scaled: bool
) -> list[Check]:
    if not pairs:
        return []
    total_a = sum(a.tables[t].row_count for t in pairs)
    total_b = sum(b.tables[t].row_count for t in pairs.values())
    out: list[Check] = []
    for ta, tb in pairs.items():
        ra, rb = a.tables[ta].row_count, b.tables[tb].row_count
        if scaled:
            sa = ra / total_a if total_a else 0.0
            sb = rb / total_b if total_b else 0.0
            diff = abs(sb - sa)
            details: dict[str, Any] = {
                "a": ra,
                "b": rb,
                "share_a": round(sa, 6),
                "share_b": round(sb, 6),
                "difference": round(diff, 6),
                "tolerance": tolerance,
                "mode": "scaled",
            }
        else:
            diff = abs(rb - ra) / ra if ra else (0.0 if rb == 0 else math.inf)
            details = {
                "a": ra,
                "b": rb,
                "difference": None if math.isinf(diff) else round(diff, 6),
                "tolerance": tolerance,
                "mode": "absolute",
            }
        out.append(Check("row_counts", _status(diff <= tolerance), ta, details=details))
    return out
