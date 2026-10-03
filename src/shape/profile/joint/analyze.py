"""Joint (multi-column) analysis of one table, bounded (#47).

``analyze_table`` reads a deterministic row sample (a :class:`Budget`: at most 20,000 rows for a
table that small, 5,000 for a larger one) and a bounded number of columns of each role, so its cost
does not grow with the table: approximate
functional dependencies and candidate keys, association measures for every type pair, conditional
probability tables for strongly associated categorical pairs, and the share of rows that break a
strong dependency (the table's ``implausible_rate``). The result is JSON-safe and additive: it is
the ``joint`` entry of a table profile.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from . import measures as M
from .placeholders import detect_placeholders

JOINT_VERSION = 1
NUMERIC_AS_CATEGORY = 50  # a float column is also a categorical view up to this many values
MIN_FD_CONFIDENCE = 0.8
MIN_FD_LIFT = 0.3  # (confidence - baseline) / (1 - baseline): beats guessing the dependent's mode
MIN_REPEAT_GROUPS = 5  # determinant values seen twice or more, below which a dependency is vacuous
IMPLAUSIBLE_FD_CONFIDENCE = (
    0.95  # a dependency this strong (and not exact) makes its exceptions implausible
)
MAX_DEPENDENCIES = 40
MAX_ASSOCIATIONS = 60
KENDALL_PAIRS = 8  # Kendall's tau (quadratic) for this many of the strongest numeric pairs
KENDALL_MIN_SPEARMAN = 0.3
MIN_ASSOCIATION = 0.1
MAX_CONDITIONALS = 10
CONDITIONAL_MIN_V = 0.25
CONDITIONAL_MAX_LEVELS = 30
CONDITIONAL_TOP = 8
MAX_VIOLATIONS = 3


@dataclass(frozen=True)
class Budget:
    """How much a table's joint analysis may read and compute. A table of at most
    ``sample_rows`` rows is analysed whole; a larger one on a deterministic sample, with fewer
    columns and pairs, so the cost stays a small, fixed share of profiling it."""

    sample_rows: int
    max_columns: int  # columns analysed per role (categorical, numeric)
    max_fd_pairs: int
    max_key_pairs: int
    assoc_columns: int  # columns per role in the association pairs


SMALL = Budget(
    sample_rows=20_000, max_columns=16, max_fd_pairs=240, max_key_pairs=120, assoc_columns=12
)
LARGE = Budget(
    sample_rows=5_000, max_columns=10, max_fd_pairs=40, max_key_pairs=20, assoc_columns=6
)
MAX_LEVELS = SMALL.sample_rows  # distinct values up to this many make a categorical view


def budget_for(row_count: int) -> Budget:
    return SMALL if row_count <= SMALL.sample_rows else LARGE


_NUMERIC_KINDS = ("int", "uint64", "float", "dt64", "objdec")
_CATEGORY_KINDS = ("int", "uint64", "float", "str", "bool", "objbool", "objdate", "objdec", "cat")


class _View:
    """One column of the sample: its categorical view (codes, labels) and/or numeric view."""

    __slots__ = ("_qcodes", "_ranks", "codes", "dictionary", "name", "nn", "values")

    def __init__(self, name: str) -> None:
        self.name = name
        self.codes: np.ndarray | None = None
        self.dictionary: Any = None
        self.values: np.ndarray | None = None
        self.nn = 0
        self._ranks: np.ndarray | None = None
        self._qcodes: tuple[np.ndarray, int] | None = None

    @property
    def k(self) -> int:
        return 0 if self.dictionary is None else len(self.dictionary)

    def label(self, i: int) -> str:
        """The ``i``-th distinct value as text (decoded when asked: most are never needed)."""
        return str(self.dictionary[i].as_py())

    @property
    def ranks(self) -> np.ndarray:
        """The numeric view's ranks (average ranks of the non-missing values, NaN elsewhere)."""
        if self._ranks is None:
            assert self.values is not None
            ok = ~np.isnan(self.values)
            r = np.full(len(self.values), np.nan)
            r[ok] = M.ranks(self.values[ok])
            self._ranks = r
        return self._ranks

    @property
    def qcodes(self) -> tuple[np.ndarray, int]:
        """The numeric view as quantile-bin codes and the bin count."""
        if self._qcodes is None:
            assert self.values is not None
            self._qcodes = M.quantile_codes(self.values)
        return self._qcodes


def _take(arr: Any, idx: np.ndarray | None) -> Any:
    if isinstance(arr, np.ndarray):
        a = arr if idx is None else arr[idx]
        return pa.array(a, from_pandas=True)
    if idx is None:
        return arr.combine_chunks() if isinstance(arr, pa.ChunkedArray) else arr
    if not isinstance(arr, pa.ChunkedArray):
        return arr.take(pa.array(idx))
    # ``idx`` is sorted: take from each chunk its own rows, so nothing is concatenated first and
    # no index is resolved against a chunk list (both cost more than the analysis on a big table)
    parts: list[Any] = []
    start = 0
    for chunk in arr.chunks:
        lo, hi = np.searchsorted(idx, [start, start + len(chunk)])
        if hi > lo:
            parts.append(chunk.take(pa.array(idx[lo:hi] - start)))
        start += len(chunk)
    if not parts:
        return arr.slice(0, 0)
    return pa.concat_arrays(parts) if len(parts) > 1 else parts[0]


def _build_view(name: str, kind: str, arr: Any, idx: np.ndarray | None) -> _View | None:
    a = _take(arr, idx)
    if pa.types.is_dictionary(a.type):
        a = a.dictionary_decode()
    if pa.types.is_floating(a.type):  # a NaN is a missing value, never a category (#313)
        a = pc.if_else(pc.is_nan(a), pa.scalar(None, a.type), a)
    n = len(a)
    nn = n - a.null_count
    if nn < 3:
        return None
    v = _View(name)
    v.nn = nn
    if kind in _CATEGORY_KINDS:
        try:
            d = pc.dictionary_encode(a)
        except pa.ArrowNotImplementedError:  # a type Arrow cannot hash: no categorical view
            d = None
        k = 0 if d is None else len(d.dictionary)
        limit = NUMERIC_AS_CATEGORY if kind in ("float", "objdec") else MAX_LEVELS
        if d is not None and 2 <= k <= limit:
            v.codes = pc.fill_null(d.indices, -1).to_numpy(zero_copy_only=False).astype(np.int64)
            v.dictionary = d.dictionary
    if kind in _NUMERIC_KINDS:
        try:
            src = pc.cast(a, pa.int64()) if kind == "dt64" else a
            # unsafe: integers past 2**53 (every ns timestamp after 1970-04) round to the
            # nearest double instead of failing the cast and losing the view (#151)
            vals = pc.cast(src, pa.float64(), safe=False).to_numpy(zero_copy_only=False)
            vals = vals.astype(np.float64)
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
            vals = None
        if vals is not None:
            vals = np.where(np.isfinite(vals), vals, np.nan)
            if np.count_nonzero(~np.isnan(vals)) > 1 and np.nanstd(vals) > 0:
                v.values = vals
    if v.codes is None and v.values is None:
        return None
    return v


def _sample_index(row_count: int, budget: Budget) -> np.ndarray | None:
    if row_count <= budget.sample_rows:
        return None
    # evenly spread rows with a fixed random offset inside each stride: deterministic, ordered,
    # and linear in the sample (a permutation of the whole table would cost more than the analysis).
    # Slot i picks one row of its own bucket [lo_i, lo_{i+1}), so no row is picked twice (#150).
    k = budget.sample_rows
    edges = (np.arange(k + 1, dtype=np.int64) * row_count) // k
    jitter = np.random.RandomState(7).random_sample(k)
    return edges[:-1] + (jitter * (edges[1:] - edges[:-1])).astype(np.int64)


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
                "determinant_value": va.label(g),
                "rows": int(tot[pos]),
                "distinct_dependents": int(distinct[pos]),
                "dependent_values": {vb.label(int(vals[i])): int(counts[i]) for i in top},
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
        shares = {str(x): s for x, s in zip(labels, top.values(), strict=True)}
        found = detect_placeholders(
            shares, null_rate=1.0 - v.nn / n_rows, cardinality=v.k, row_count=n_rows
        )
        for f in found:
            code = list(top)[list(shares).index(f["value"])]
            rows |= v.codes == code
    return rows


def _dependencies(
    cats: list[_View], n_rows: int, budget: Budget
) -> tuple[list[dict[str, Any]], np.ndarray, int]:
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
            if vb is va or evaluated >= budget.max_fd_pairs:
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


def _keys(cats: list[_View], n_rows: int, sampled: bool, budget: Budget) -> list[dict[str, Any]]:
    """Two-column candidate keys: unique together, neither unique alone."""
    out: list[dict[str, Any]] = []
    pairs = 0
    singles = {v.name for v in cats if v.k >= v.nn}
    for i, va in enumerate(cats):
        for vb in cats[i + 1 :]:
            if va.name in singles or vb.name in singles or pairs >= budget.max_key_pairs:
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
    cats: list[_View], nums: list[_View], n_rows: int, budget: Budget
) -> tuple[list[dict[str, Any]], dict[tuple[str, str], np.ndarray]]:
    out: list[dict[str, Any]] = []
    tables: dict[tuple[str, str], np.ndarray] = {}
    small = [v for v in cats if v.k <= 200][: budget.assoc_columns]
    nums = nums[: budget.assoc_columns]
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
    numeric: list[tuple[float, dict[str, Any], _View, _View]] = []
    for i, va in enumerate(nums):
        assert va.values is not None
        for vb in nums[i + 1 :]:
            assert vb.values is not None
            both = ~(np.isnan(va.values) | np.isnan(vb.values))
            p = M.pearson(va.values, vb.values)
            ra, rb = va.ranks, vb.ranks
            s_ = M.pearson(ra, rb) if both.all() else M.spearman(va.values, vb.values)
            strength = max(abs(p or 0.0), abs(s_ or 0.0))
            if strength < MIN_ASSOCIATION:
                continue
            ca, ka = va.qcodes
            cb, kb = vb.qcodes
            mi = M.mutual_information(M.contingency(ca, cb, max(ka, 1), max(kb, 1)))
            entry = {
                "a": va.name,
                "b": vb.name,
                "kind": "numeric",
                "rows": int(both.sum()),
                "pearson": _round(p),
                "spearman": _round(s_),
                "kendall": None,
                "mutual_information": _round(mi),
                "_strength": strength,
            }
            numeric.append((abs(s_ or 0.0), entry, va, vb))
    numeric.sort(key=lambda t: -t[0])
    for rank, (sp, entry, va, vb) in enumerate(numeric):
        if rank < KENDALL_PAIRS and sp >= KENDALL_MIN_SPEARMAN:
            assert va.values is not None and vb.values is not None
            entry["kendall"] = _round(M.kendall_tau(va.values, vb.values))
        out.append(entry)
    for vc in small:
        assert vc.codes is not None
        for vn in nums:
            assert vn.values is not None
            if vn.name == vc.name:
                continue
            eta = M.correlation_ratio(vc.codes, vn.values, vc.k)
            if eta < MIN_ASSOCIATION:
                continue
            cn, kn = vn.qcodes
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
                rows[given.label(int(gi))] = {
                    "n": total,
                    "p": {
                        target.label(int(j)): _round(tab[gi, j] / total, 4)
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
    budget = budget_for(row_count)
    idx = _sample_index(row_count, budget)
    n_rows = row_count if idx is None else len(idx)
    views: dict[str, _View] = {}
    cats: list[_View] = []
    nums: list[_View] = []
    for c in cols:
        # a view is built only for a role that still has room (#326): once the categorical role
        # is full, a text column (which has no numeric role) costs nothing, however wide the table
        wants_cat = c.kind in _CATEGORY_KINDS and len(cats) < budget.max_columns
        wants_num = c.kind in _NUMERIC_KINDS and len(nums) < budget.max_columns
        if not (wants_cat or wants_num):
            if len(cats) >= budget.max_columns and len(nums) >= budget.max_columns:
                break
            continue
        v = _build_view(c.name, c.kind, c.arr, idx)
        if v is None:
            continue
        # a unique key (every value different) joins no association; it stays a determinant
        views[c.name] = v
        if v.codes is not None and len(cats) < budget.max_columns:
            cats.append(v)
        if v.values is not None and v.k != v.nn and len(nums) < budget.max_columns:
            nums.append(v)
    if len(cats) + len(nums) < 2:
        return None
    sampled = idx is not None
    deps, by_dependency, evaluated = _dependencies(cats, n_rows, budget)
    by_placeholder = _placeholder_rows(cats, n_rows)
    flagged = by_dependency | by_placeholder
    keys = _keys(cats, n_rows, sampled, budget)
    assoc_cats = [v for v in cats if v.k < v.nn]
    assoc_nums = [v for v in nums if v.values is not None and np.isfinite(v.values).sum() > 10]
    assoc, tables = _associations(assoc_cats, assoc_nums, n_rows, budget)
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
