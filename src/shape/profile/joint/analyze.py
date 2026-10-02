"""Joint (multi-column) analysis of one table, bounded (#47).

``analyze_table`` reads a deterministic row sample of at most ``SAMPLE_ROWS`` rows and at most
``MAX_COLUMNS`` columns of each role, so its cost does not grow with the table: approximate
functional dependencies and candidate keys, association measures for every type pair, conditional
probability tables for strongly associated categorical pairs, and the share of rows that break a
strong dependency (the table's ``implausible_rate``). The result is JSON-safe and additive: it is
the ``joint`` entry of a table profile.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from . import measures as M
from .placeholders import detect_placeholders

JOINT_VERSION = 1
SAMPLE_ROWS = 20_000  # rows analysed (a deterministic sample beyond this)
MAX_COLUMNS = 16  # columns analysed per role (categorical, numeric)
MAX_LEVELS = SAMPLE_ROWS  # distinct values up to this many make a categorical view
NUMERIC_AS_CATEGORY = 50  # a float column is also a categorical view up to this many values
MAX_FD_PAIRS = 240
MIN_FD_CONFIDENCE = 0.8
MIN_FD_LIFT = 0.3  # (confidence - baseline) / (1 - baseline): beats guessing the dependent's mode
MIN_REPEAT_GROUPS = 5  # determinant values seen twice or more, below which a dependency is vacuous
IMPLAUSIBLE_FD_CONFIDENCE = (
    0.95  # a dependency this strong (and not exact) makes its exceptions implausible
)
MAX_DEPENDENCIES = 40
MAX_KEY_PAIRS = 120
MAX_ASSOCIATIONS = 60
MIN_ASSOCIATION = 0.1
MAX_CONDITIONALS = 10
CONDITIONAL_MIN_V = 0.25
CONDITIONAL_MAX_LEVELS = 30
CONDITIONAL_TOP = 8
MAX_VIOLATIONS = 3
_NUMERIC_KINDS = ("int", "uint64", "float", "dt64")
_CATEGORY_KINDS = ("int", "uint64", "float", "str", "bool")


class _View:
    """One column of the sample: its categorical view (codes, labels) and/or numeric view."""

    __slots__ = ("_labels", "codes", "dictionary", "name", "nn", "values")

    def __init__(self, name: str) -> None:
        self.name = name
        self.codes: np.ndarray | None = None
        self.dictionary: Any = None
        self._labels: list[str] | None = None
        self.values: np.ndarray | None = None
        self.nn = 0

    @property
    def k(self) -> int:
        return 0 if self.dictionary is None else len(self.dictionary)

    @property
    def labels(self) -> list[str]:
        """The distinct values as text, decoded on first use (most columns never need them)."""
        if self._labels is None:
            self._labels = [_label(x) for x in self.dictionary.to_pylist()]
        return self._labels


def _take(arr: Any, idx: np.ndarray | None) -> Any:
    if isinstance(arr, np.ndarray):
        a = arr if idx is None else arr[idx]
        return pa.array(a, from_pandas=True)
    if isinstance(arr, pa.ChunkedArray):
        arr = arr.combine_chunks()
    return arr if idx is None else arr.take(pa.array(idx))


def _label(v: Any) -> str:
    return str(v)


def _build_view(name: str, kind: str, arr: Any, idx: np.ndarray | None) -> _View | None:
    a = _take(arr, idx)
    n = len(a)
    nn = n - a.null_count
    if nn < 3:
        return None
    v = _View(name)
    v.nn = nn
    if kind in _CATEGORY_KINDS:
        d = pc.dictionary_encode(a)
        k = len(d.dictionary)
        limit = NUMERIC_AS_CATEGORY if kind == "float" else MAX_LEVELS
        if 2 <= k <= limit:
            v.codes = pc.fill_null(d.indices, -1).to_numpy(zero_copy_only=False).astype(np.int64)
            v.dictionary = d.dictionary
    if kind in _NUMERIC_KINDS:
        try:
            src = pc.cast(a, pa.int64()) if kind == "dt64" else a
            vals = pc.cast(src, pa.float64()).to_numpy(zero_copy_only=False).astype(np.float64)
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
            vals = None
        if vals is not None:
            vals = np.where(np.isfinite(vals), vals, np.nan)
            if np.nanstd(vals) > 0:
                v.values = vals
    if v.codes is None and v.values is None:
        return None
    return v


def _sample_index(row_count: int) -> np.ndarray | None:
    if row_count <= SAMPLE_ROWS:
        return None
    return np.sort(np.random.RandomState(7).choice(row_count, size=SAMPLE_ROWS, replace=False))


def _round(x: float | None, digits: int = 4) -> float | None:
    return None if x is None or not np.isfinite(x) else round(float(x), digits)


def _violations(
    aa: np.ndarray, bb: np.ndarray, va: _View, vb: _View, st: M.FdStats
) -> list[dict[str, Any]]:
    """The groups that break the dependency most: rows outside the group's modal value."""
    ix, tot, mx, distinct = st.group_ix, st.total, st.most, st.distinct
    excess = tot - mx
    out: list[dict[str, Any]] = []
    for pos in np.argsort(-excess, kind="stable")[:MAX_VIOLATIONS]:
        if distinct[pos] < 2:
            break
        g = int(ix[pos])
        vals, counts = np.unique(bb[aa == g], return_counts=True)
        top = np.argsort(-counts, kind="stable")[:3]
        out.append(
            {
                "determinant_value": va.labels[g],
                "rows": int(tot[pos]),
                "distinct_dependents": int(distinct[pos]),
                "dependent_values": {vb.labels[int(vals[i])]: int(counts[i]) for i in top},
            }
        )
    return out


def _placeholder_rows(cats: list[_View], n_rows: int) -> np.ndarray:
    """The rows that hold a placeholder value in a categorical view (judged on the sample)."""
    rows = np.zeros(n_rows, dtype=bool)
    for v in cats:
        assert v.codes is not None
        counts = np.bincount(v.codes[v.codes >= 0], minlength=v.k)
        order = np.argsort(-counts, kind="stable")[:500]
        top = {int(i): counts[i] / v.nn for i in order if counts[i] > 0}
        labels = v.dictionary.take(pa.array(list(top))).to_pylist()
        shares = {_label(x): s for x, s in zip(labels, top.values(), strict=True)}
        found = detect_placeholders(
            shares, null_rate=1.0 - v.nn / n_rows, cardinality=v.k, row_count=n_rows
        )
        for f in found:
            code = list(top)[list(shares).index(f["value"])]
            rows |= v.codes == code
    return rows


def _dependencies(cats: list[_View], n_rows: int) -> tuple[list[dict[str, Any]], np.ndarray, int]:
    """Approximate functional dependencies ``a -> b`` among the categorical views, and the rows
    that are in the minority of a strong one."""
    found: list[dict[str, Any]] = []
    flagged = np.zeros(n_rows, dtype=bool)
    evaluated = 0
    for va in cats:
        assert va.codes is not None
        if va.k >= va.nn:  # every value different: a key, which determines everything vacuously
            continue
        for vb in cats:
            if vb is va or evaluated >= MAX_FD_PAIRS:
                continue
            assert vb.codes is not None
            evaluated += 1
            st = M.fd_stats(va.codes, vb.codes, va.k, vb.k)
            if st is None:
                continue
            conf, base = st.confidence, st.baseline
            lift = (conf - base) / (1.0 - base) if base < 1.0 else 0.0
            if conf < MIN_FD_CONFIDENCE or lift < MIN_FD_LIFT:
                continue
            if st.repeat_groups < MIN_REPEAT_GROUPS:
                continue
            m = (va.codes >= 0) & (vb.codes >= 0)
            aa, bb = va.codes[m], vb.codes[m]
            entry: dict[str, Any] = {
                "determinant": [va.name],
                "dependent": vb.name,
                "confidence": _round(conf, 6),
                "baseline": _round(base, 6),
                "support": _round(st.support, 6),
                "rows": st.rows,
                "groups": st.groups,
                "repeat_groups": st.repeat_groups,
                "violating_groups": st.violating_groups,
                "violations": _violations(aa, bb, va, vb, st) if st.violating_groups else [],
            }
            found.append(entry)
            if conf >= IMPLAUSIBLE_FD_CONFIDENCE and conf < 1.0:
                mode = st.mode[aa]
                tot_by_g = np.zeros(va.k, dtype=np.int64)
                tot_by_g[st.group_ix] = st.total
                minority = (bb != mode) & (tot_by_g[aa] >= 2)
                flagged[np.flatnonzero(m)[minority]] = True
    found.sort(key=lambda e: (-e["confidence"], -e["support"], e["determinant"], e["dependent"]))
    return found[:MAX_DEPENDENCIES], flagged, evaluated


def _keys(cats: list[_View], n_rows: int, sampled: bool) -> list[dict[str, Any]]:
    """Two-column candidate keys: unique together, neither unique alone."""
    out: list[dict[str, Any]] = []
    pairs = 0
    singles = {v.name for v in cats if v.k >= v.nn}
    for i, va in enumerate(cats):
        for vb in cats[i + 1 :]:
            if va.name in singles or vb.name in singles or pairs >= MAX_KEY_PAIRS:
                continue
            assert va.codes is not None and vb.codes is not None
            if va.k * vb.k < n_rows:  # fewer combinations than rows: cannot be unique
                continue
            pairs += 1
            m = (va.codes >= 0) & (vb.codes >= 0)
            if int(m.sum()) != n_rows:  # a null in a key column: not a key
                continue
            key = va.codes[m] * vb.k + vb.codes[m]
            if len(np.unique(key)) == n_rows:
                out.append(
                    {
                        "fields": [va.name, vb.name],
                        "rows": n_rows,
                        "distinct": n_rows,
                        "exact": not sampled,
                    }
                )
    return out


def _binned(v: _View) -> tuple[np.ndarray, int]:
    if v.codes is not None and v.k <= 30:
        return v.codes, v.k
    assert v.values is not None
    return M.quantile_codes(v.values)


def _associations(
    cats: list[_View], nums: list[_View], n_rows: int
) -> tuple[list[dict[str, Any]], dict[tuple[str, str], np.ndarray]]:
    out: list[dict[str, Any]] = []
    tables: dict[tuple[str, str], np.ndarray] = {}
    small = [v for v in cats if v.k <= 200]
    for i, va in enumerate(small):
        for vb in small[i + 1 :]:
            assert va.codes is not None and vb.codes is not None
            if va.k * vb.k > M.DENSE_CELLS:
                continue
            t = M.contingency(va.codes, vb.codes, va.k, vb.k)
            n = int(t.sum())
            if n < 10:
                continue
            v = M.cramers_v(t)
            u_ab, u_ba = M.theil_u(t)  # (U(a | b), U(b | a))
            mi = M.mutual_information(t)
            hmin = min(
                M._entropy(t.sum(axis=1) / n),
                M._entropy(t.sum(axis=0) / n),
            )
            strength = max(v, u_ab, u_ba)
            if strength < MIN_ASSOCIATION:
                continue
            out.append(
                {
                    "a": va.name,
                    "b": vb.name,
                    "kind": "categorical",
                    "rows": n,
                    "cramers_v": _round(v),
                    "theil_u_a_given_b": _round(u_ab),
                    "theil_u_b_given_a": _round(u_ba),
                    "mutual_information": _round(mi),
                    "normalized_mi": _round(mi / hmin if hmin > 0 else 0.0),
                    "_strength": strength,
                }
            )
            tables[(va.name, vb.name)] = t
    for i, va in enumerate(nums):
        assert va.values is not None
        for vb in nums[i + 1 :]:
            assert vb.values is not None
            p = M.pearson(va.values, vb.values)
            s = M.spearman(va.values, vb.values)
            strength = max(abs(p or 0.0), abs(s or 0.0))
            if strength < MIN_ASSOCIATION:
                continue
            ca, ka = M.quantile_codes(va.values)
            cb, kb = M.quantile_codes(vb.values)
            mi = M.mutual_information(M.contingency(ca, cb, max(ka, 1), max(kb, 1)))
            out.append(
                {
                    "a": va.name,
                    "b": vb.name,
                    "kind": "numeric",
                    "rows": int((~(np.isnan(va.values) | np.isnan(vb.values))).sum()),
                    "pearson": _round(p),
                    "spearman": _round(s),
                    "kendall": _round(M.kendall_tau(va.values, vb.values)),
                    "mutual_information": _round(mi),
                    "_strength": strength,
                }
            )
    for vc in small:
        assert vc.codes is not None
        for vn in nums:
            assert vn.values is not None
            if vn.name == vc.name:
                continue
            eta = M.correlation_ratio(vc.codes, vn.values, vc.k)
            if eta < MIN_ASSOCIATION:
                continue
            cn, kn = M.quantile_codes(vn.values)
            mi = M.mutual_information(M.contingency(vc.codes, cn, vc.k, max(kn, 1)))
            out.append(
                {
                    "a": vc.name,
                    "b": vn.name,
                    "kind": "categorical-numeric",
                    "rows": int(((vc.codes >= 0) & ~np.isnan(vn.values)).sum()),
                    "correlation_ratio": _round(eta),
                    "mutual_information": _round(mi),
                    "_strength": eta,
                }
            )
    out.sort(key=lambda e: (-e["_strength"], e["a"], e["b"]))
    return out[:MAX_ASSOCIATIONS], tables


def _conditionals(
    assoc: list[dict[str, Any]],
    tables: dict[tuple[str, str], np.ndarray],
    views: dict[str, _View],
) -> list[dict[str, Any]]:
    """``P(target | given)`` for strongly associated categorical pairs of few values, in both
    directions where both are small."""
    out: list[dict[str, Any]] = []
    for e in assoc:
        if e["kind"] != "categorical" or (e["cramers_v"] or 0) < CONDITIONAL_MIN_V:
            continue
        t = tables.get((e["a"], e["b"]))
        if t is None or max(t.shape) > CONDITIONAL_MAX_LEVELS:
            continue
        va, vb = views[e["a"]], views[e["b"]]
        for given, target, tab in ((va, vb, t), (vb, va, t.T)):
            if len(out) >= MAX_CONDITIONALS:
                return out
            rows: dict[str, Any] = {}
            for gi in np.argsort(-tab.sum(axis=1), kind="stable"):
                total = int(tab[gi].sum())
                if total == 0:
                    continue
                order = np.argsort(-tab[gi], kind="stable")[:CONDITIONAL_TOP]
                rows[given.labels[int(gi)]] = {
                    "n": total,
                    "p": {
                        target.labels[int(j)]: _round(tab[gi, j] / total, 4)
                        for j in order
                        if tab[gi, j] > 0
                    },
                }
            out.append(
                {
                    "given": given.name,
                    "target": target.name,
                    "cramers_v": e["cramers_v"],
                    "table": rows,
                }
            )
    return out


def analyze_table(cols: list[Any], row_count: int) -> dict[str, Any] | None:
    """The ``joint`` entry of a table profile, or None when the table has too few columns or rows
    for a joint analysis. ``cols`` are the reader's column objects (``name``, ``kind``, ``arr``)."""
    if len(cols) < 2 or row_count < 10:
        return None
    idx = _sample_index(row_count)
    n_rows = row_count if idx is None else len(idx)
    views: dict[str, _View] = {}
    cats: list[_View] = []
    nums: list[_View] = []
    for c in cols:
        if c.kind not in set(_NUMERIC_KINDS) | set(_CATEGORY_KINDS):
            continue
        if len(cats) >= MAX_COLUMNS and len(nums) >= MAX_COLUMNS:
            break
        v = _build_view(c.name, c.kind, c.arr, idx)
        if v is None:
            continue
        # a unique key (every value different) joins no association; it stays a determinant
        views[c.name] = v
        if v.codes is not None and len(cats) < MAX_COLUMNS:
            cats.append(v)
        if v.values is not None and v.k != v.nn and len(nums) < MAX_COLUMNS and v.nn < n_rows * 2:
            nums.append(v)
    if len(cats) + len(nums) < 2:
        return None
    sampled = idx is not None
    deps, by_dependency, evaluated = _dependencies(cats, n_rows)
    by_placeholder = _placeholder_rows(cats, n_rows)
    flagged = by_dependency | by_placeholder
    keys = _keys(cats, n_rows, sampled)
    assoc_cats = [v for v in cats if v.k < v.nn]
    assoc_nums = [v for v in nums if v.values is not None and np.isfinite(v.values).sum() > 10]
    assoc, tables = _associations(assoc_cats, assoc_nums, n_rows)
    conds = _conditionals(assoc, tables, views)
    for e in assoc:
        e.pop("_strength", None)
    return {
        "version": JOINT_VERSION,
        "rows_analyzed": n_rows,
        "sampled": sampled,
        "columns": sorted({v.name for v in cats} | {v.name for v in nums}),
        "dependencies": deps,
        "dependency_pairs_evaluated": evaluated,
        "keys": keys,
        "associations": assoc,
        "conditionals": conds,
        "implausible_rate": _round(float(flagged.sum()) / n_rows, 6),
        "implausible_by_dependency": _round(float(by_dependency.sum()) / n_rows, 6),
        "implausible_by_placeholder": _round(float(by_placeholder.sum()) / n_rows, 6),
    }
