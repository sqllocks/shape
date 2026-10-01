"""Tier 3: research-grade checks and tools (experimental).

* :class:`ChowLiuTree` learns the dependency structure of a table (the tree of column pairs with
  the highest mutual information); :func:`compare_trees` says how well a synthetic table keeps
  the reference's structure (``shape fidelity --tier 3``);
* :class:`DriftMonitor` tests two tables for drift: KS test (numbers), chi-squared (everything
  else) and the population stability index; :func:`psi_report` is the PSI alone and needs no
  SciPy (``shape drift --psi``);
* :func:`bootstrap_table` resamples a table's rows with replacement and jitters its numbers (the
  library form of the ``bootstrap`` generation strategy).

A port of the reference implementation's tier 3 (parity harness:
``benchmarks/vs_spindle/fidelity_tiers_1to1``). Differences, all on purpose:

* the KS test needs SciPy and raises an error naming the extra when it is missing (the reference
  returns "no drift" for every column, which would hide real drift);
* ``psi_report`` also scores timestamps and text columns of at most 50 distinct values (PSI over
  category shares), which the reference's PSI does not;
* a timestamp column with missing values is filled with its median before binning (the reference
  fails on one);
* ``bootstrap_table(n_rows=0)`` returns no rows (the reference treats 0 as "same size").
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]

from shape.generation.report.compare import chi2_sf

from ._frame import Column, Frame, as_frame, sample_positions
from .tier1 import INSTALL_HINT, clean
from .tier2 import INTERNAL_PREFIX

# -- Chow-Liu dependency tree -----------------------------------------------------------------


@dataclass
class BayesianEdge:
    """An edge of the tree (``parent`` and ``child`` are only the order the pair was found in)."""

    parent: str
    child: str
    mutual_information: float


@dataclass
class ChowLiuResult:
    edges: list[BayesianEdge]
    column_order: list[str]
    mutual_info_matrix: dict[str, dict[str, float]]

    def to_dict(self) -> dict[str, Any]:
        return dict(clean(asdict(self)))


def _cut(x: npt.NDArray[np.float64], n_bins: int) -> npt.NDArray[np.int64]:
    """Equal-width bin of every value (0 to ``n_bins - 1``): ``pandas.cut(x, n_bins, labels=False)``
    (the first edge moves down by 0.1% of the range so the minimum falls in bin 0)."""
    mn, mx = float(x.min()), float(x.max())
    if mn == mx:
        mn -= 0.001 * abs(mn) if mn != 0 else 0.001
        mx += 0.001 * abs(mx) if mx != 0 else 0.001
        bins = np.linspace(mn, mx, n_bins + 1, endpoint=True)
    else:
        bins = np.linspace(mn, mx, n_bins + 1, endpoint=True)
        bins[0] -= (mx - mn) * 0.001
    return np.asarray(bins.searchsorted(x, side="left") - 1, dtype=np.int64)


class ChowLiuTree:
    """Learn a tree structure over the columns with the Chow-Liu algorithm: the maximum spanning
    tree of the pairwise mutual information of the (binned) columns.

    Numbers and timestamps are cut into ``n_bins`` equal-width bins (a missing value takes the
    median first), anything else is coded by sorted value; the first ``sample_size`` rows are used.
    """

    def __init__(self, n_bins: int = 10, sample_size: int = 2000) -> None:
        self.n_bins = n_bins
        self.sample_size = sample_size

    def fit(self, data: pa.Table | Frame) -> ChowLiuResult:
        frame = as_frame(data)
        encoded = {c.name: self._encode(c, self.sample_size) for c in frame}
        columns = list(encoded)
        mi: dict[str, dict[str, float]] = {c: {} for c in columns}
        for i, ci in enumerate(columns):
            for cj in columns[i + 1 :]:
                m = _mutual_information(encoded[ci], encoded[cj])
                mi[ci][cj] = m
                mi[cj][ci] = m
        return ChowLiuResult(_max_spanning_tree(columns, mi), columns, mi)

    def _encode(self, col: Column, head: int) -> npt.NDArray[np.int64]:
        sliced = Column(col.name, col.kind, col.values[:head], col.valid[:head])
        if sliced.is_numeric or sliced.is_datetime:
            v = sliced.values.astype(np.float64)
            if not sliced.valid.all():
                ok = v[sliced.valid]
                v = np.where(sliced.valid, v, np.median(ok) if ok.size else 0.0)
            return _cut(v, self.n_bins) if v.size else np.zeros(0, dtype=np.int64)
        return sliced.codes()


def _mutual_information(x: npt.NDArray[np.int64], y: npt.NDArray[np.int64]) -> float:
    if x.size == 0:
        return 0.0
    xs, xi = np.unique(x, return_inverse=True)
    ys, yi = np.unique(y, return_inverse=True)
    counts = np.zeros((xs.size, ys.size), dtype=np.float64)
    np.add.at(counts, (xi, yi), 1.0)
    joint = counts / counts.sum()
    px, py = joint.sum(axis=1), joint.sum(axis=0)
    nz = joint > 0
    terms = joint[nz] * np.log(joint[nz] / (np.outer(px, py)[nz] + 1e-12) + 1e-12)
    return float(max(0.0, terms.sum()))


def _max_spanning_tree(
    columns: Sequence[str], mi: dict[str, dict[str, float]]
) -> list[BayesianEdge]:
    if len(columns) < 2:
        return []
    all_edges: list[tuple[float, str, str]] = []
    for i, ci in enumerate(columns):
        for cj in columns[i + 1 :]:
            all_edges.append((mi[ci].get(cj, 0.0), ci, cj))
    all_edges.sort(reverse=True)
    parent = {c: c for c in columns}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    edges: list[BayesianEdge] = []
    for value, ci, cj in all_edges:
        rx, ry = find(ci), find(cj)
        if rx != ry:
            parent[rx] = ry
            edges.append(BayesianEdge(ci, cj, value))
        if len(edges) == len(columns) - 1:
            break
    return edges


def compare_trees(real: ChowLiuResult, synthetic: ChowLiuResult) -> dict[str, Any]:
    """How much of the reference's dependency structure the synthetic table keeps: the Jaccard
    overlap of the two trees' edge sets, and the mean and largest absolute difference of mutual
    information over the column pairs both tables have."""

    def pairs(r: ChowLiuResult) -> set[frozenset[str]]:
        return {frozenset((e.parent, e.child)) for e in r.edges}

    a, b = pairs(real), pairs(synthetic)
    shared = [c for c in real.column_order if c in synthetic.mutual_info_matrix]
    diffs = [
        abs(real.mutual_info_matrix[ci][cj] - synthetic.mutual_info_matrix[ci][cj])
        for i, ci in enumerate(shared)
        for cj in shared[i + 1 :]
    ]
    union = a | b
    return {
        "edge_overlap": len(a & b) / len(union) if union else 1.0,
        "mutual_information_mean_abs_diff": float(np.mean(diffs)) if diffs else 0.0,
        "mutual_information_max_abs_diff": float(np.max(diffs)) if diffs else 0.0,
        "columns_compared": len(shared),
    }


# -- Drift --------------------------------------------------------------------------------------


@dataclass
class ColumnDriftResult:
    column: str
    drift_score: float
    test_statistic: float | None
    p_value: float | None
    is_drifted: bool
    method: str  # "ks", "chi2", "psi" or "error"
    psi: float | None = None


@dataclass
class DriftReport:
    columns: dict[str, ColumnDriftResult]
    drifted_columns: list[str]
    drift_fraction: float
    overall_drift_score: float
    skipped: dict[str, str] = field(default_factory=dict)  # column -> why it was not scored

    def to_dict(self) -> dict[str, Any]:
        return dict(clean(asdict(self)))


def population_stability_index(
    expected: npt.ArrayLike, actual: npt.ArrayLike, n_bins: int = 10
) -> float:
    """PSI between two numeric samples, over ``n_bins`` equal-width bins of their joint range."""
    e = np.asarray(expected, dtype=np.float64)
    a = np.asarray(actual, dtype=np.float64)
    mn, mx = min(e.min(), a.min()), max(e.max(), a.max())
    if mn == mx:
        return 0.0
    bins = np.linspace(mn, mx, n_bins + 1)
    exp_pct = np.histogram(e, bins=bins)[0] / len(e) + 1e-10
    act_pct = np.histogram(a, bins=bins)[0] / len(a) + 1e-10
    return float(np.sum((act_pct - exp_pct) * np.log(act_pct / exp_pct)))


def _category_psi(expected: Sequence[str], actual: Sequence[str]) -> float:
    cats = sorted(set(expected) | set(actual))
    index = {c: i for i, c in enumerate(cats)}
    e = np.bincount([index[v] for v in expected], minlength=len(cats)) / len(expected) + 1e-10
    a = np.bincount([index[v] for v in actual], minlength=len(cats)) / len(actual) + 1e-10
    return float(np.sum((a - e) * np.log(a / e)))


def _pick(col: Column, sample_size: int) -> npt.NDArray[Any]:
    v = col.present()
    return v[sample_positions(len(v), sample_size)] if len(v) > sample_size else v


class DriftMonitor:
    """Drift between a reference table and a current one, column by column.

    A number is tested with the two-sample KS test (SciPy), anything else with a chi-squared test
    of its value counts; the PSI of every column is the second signal. A column drifts when the
    test's p-value is below ``pvalue_threshold`` or its PSI is above ``psi_threshold`` (numbers),
    or when the chi-squared p-value is below the threshold (everything else). Columns with fewer
    than 10 values on either side, and columns starting ``_shape_``, are left out."""

    def __init__(
        self, pvalue_threshold: float = 0.05, psi_threshold: float = 0.2, sample_size: int = 5000
    ) -> None:
        self.pvalue_threshold = pvalue_threshold
        self.psi_threshold = psi_threshold
        self.sample_size = sample_size

    def compare(self, reference: pa.Table | Frame, current: pa.Table | Frame) -> DriftReport:
        ref, cur = as_frame(reference), as_frame(current)
        results: dict[str, ColumnDriftResult] = {}
        for col in ref:
            if col.name not in cur or col.name.startswith(INTERNAL_PREFIX):
                continue
            other = cur[col.name]
            if col.valid.sum() < 10 or other.valid.sum() < 10:
                continue
            a, b = _pick(col, self.sample_size), _pick(other, self.sample_size)
            if col.is_numeric:
                results[col.name] = (
                    self._ks(col.name, a, b)
                    if other.is_numeric
                    else ColumnDriftResult(col.name, 0.0, None, None, True, "error")
                )
            else:
                results[col.name] = self._chi2(col.name, _as_text(col, a), _as_text(other, b))
        drifted = [c for c, r in results.items() if r.is_drifted]
        return DriftReport(
            columns=results,
            drifted_columns=drifted,
            drift_fraction=len(drifted) / len(results) if results else 0.0,
            overall_drift_score=round(
                sum(r.drift_score for r in results.values()) / len(results) if results else 0.0, 4
            ),
        )

    def _ks(self, name: str, a: npt.NDArray[Any], b: npt.NDArray[Any]) -> ColumnDriftResult:
        try:
            from scipy import stats  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                f"the KS drift test needs SciPy ({INSTALL_HINT}); `psi_report` does not"
            ) from exc
        af, bf = a.astype(np.float64), b.astype(np.float64)
        stat, p = stats.ks_2samp(af, bf)
        psi = population_stability_index(af, bf)
        return ColumnDriftResult(
            column=name,
            drift_score=float(min(1.0, stat + psi / 2)),
            test_statistic=float(stat),
            p_value=float(p),
            is_drifted=bool(float(p) < self.pvalue_threshold or psi > self.psi_threshold),
            method="ks",
            psi=psi,
        )

    def _chi2(self, name: str, ref: list[str], cur: list[str]) -> ColumnDriftResult:
        cats = sorted(set(ref) | set(cur))
        index = {c: i for i, c in enumerate(cats)}
        ref_arr = np.bincount([index[v] for v in ref], minlength=len(cats)).astype(np.float64)
        cur_arr = np.bincount([index[v] for v in cur], minlength=len(cats)).astype(np.float64)
        expected = ref_arr * (cur_arr.sum() / (ref_arr.sum() + 1e-10))
        observed, exp = cur_arr + 1, expected + 1
        if not np.isclose(observed.sum(), exp.sum(), rtol=1e-8):
            # the totals must agree for a chi-squared test: fail closed, as a failed test must
            return ColumnDriftResult(name, 0.0, None, None, True, "error")
        stat = float(np.sum((observed - exp) ** 2 / exp))
        p = chi2_sf(stat, len(cats) - 1) if len(cats) > 1 else float("nan")  # no degrees of freedom
        return ColumnDriftResult(
            column=name,
            drift_score=min(1.0, stat / (len(cats) * 10 + 1)),
            test_statistic=stat,
            p_value=p,
            is_drifted=bool(p < self.pvalue_threshold),
            method="chi2",
            psi=_category_psi(ref, cur),
        )


def _as_text(col: Column, picked: npt.NDArray[Any]) -> list[str]:
    return [str(v) for v in picked.tolist()] if col.kind != "datetime" else [
        str(int(v)) for v in picked.tolist()
    ]


MAX_PSI_CATEGORIES = 50


def psi_report(
    reference: pa.Table | Frame,
    current: pa.Table | Frame,
    threshold: float = 0.2,
    sample_size: int = 5000,
) -> DriftReport:
    """The PSI of every shared column (``method`` ``psi``; no SciPy needed): a column drifts when
    its PSI is above ``threshold``. Numbers and timestamps use 10 equal-width bins over the joint
    range; text columns use the share of each category, and a text column with more than 50 distinct
    values (identifiers, free text) is left out and named in ``skipped``, since a category share
    means nothing there."""
    ref, cur = as_frame(reference), as_frame(current)
    results: dict[str, ColumnDriftResult] = {}
    skipped: dict[str, str] = {}
    for col in ref:
        if col.name not in cur or col.name.startswith(INTERNAL_PREFIX):
            continue
        other = cur[col.name]
        if col.valid.sum() < 10 or other.valid.sum() < 10:
            continue
        a, b = _pick(col, sample_size), _pick(other, sample_size)
        ordered = col.is_numeric or col.is_datetime
        if ordered and (other.is_numeric or other.is_datetime):
            psi = population_stability_index(a.astype(np.float64), b.astype(np.float64))
        elif ordered or other.is_numeric or other.is_datetime:
            results[col.name] = ColumnDriftResult(col.name, 0.0, None, None, True, "error")
            continue
        else:
            ta, tb = _as_text(col, a), _as_text(other, b)
            if len(set(ta)) > MAX_PSI_CATEGORIES:
                skipped[col.name] = f"more than {MAX_PSI_CATEGORIES} distinct values"
                continue
            psi = _category_psi(ta, tb)
        results[col.name] = ColumnDriftResult(
            column=col.name,
            drift_score=float(min(1.0, psi)),
            test_statistic=psi,
            p_value=None,
            is_drifted=bool(psi > threshold),
            method="psi",
            psi=psi,
        )
    drifted = [c for c, r in results.items() if r.is_drifted]
    return DriftReport(
        columns=results,
        drifted_columns=drifted,
        drift_fraction=len(drifted) / len(results) if results else 0.0,
        overall_drift_score=round(
            sum(r.drift_score for r in results.values()) / len(results) if results else 0.0, 4
        ),
        skipped=skipped,
    )


# -- Bootstrap ----------------------------------------------------------------------------------


@dataclass
class BootstrapResult:
    table_name: str
    n_rows: int
    n_bootstrap_samples: int
    seed: int
    source_rows_used: int


def bootstrap_table(
    source: pa.Table,
    n_rows: int | None = None,
    table_name: str = "table",
    seed: int = 42,
    add_jitter: bool = True,
    jitter_std_fraction: float = 0.01,
) -> tuple[pa.Table, BootstrapResult]:
    """Resample ``source``'s rows with replacement (``n_rows`` of them, default as many as the
    source) and, with ``add_jitter``, add normal noise with a standard deviation of
    ``jitter_std_fraction`` of each numeric column's own to the integer and float columns (which
    become float64). Seeded: the same seed gives the same table. This is numpy's
    ``default_rng(seed)`` stream; the ``bootstrap`` generation strategy draws its rows from the
    engine's own."""
    if source.num_rows == 0:
        raise ValueError("cannot bootstrap an empty table")
    n = source.num_rows if n_rows is None else int(n_rows)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, source.num_rows, size=n)
    sampled = source.take(pa.array(idx))
    if add_jitter:
        src = Frame.from_arrow(source)
        for col in src.numbers:
            valid = col.valid[idx]
            data = col.values.astype(np.float64)
            std = float(np.std(data[col.valid], ddof=1)) if col.valid.sum() > 1 else float("nan")
            if std > 0:
                jitter = rng.normal(0, std * jitter_std_fraction, size=n)
                out = pa.array(data[idx] + jitter, mask=~valid)
                pos = sampled.column_names.index(col.name)
                sampled = sampled.set_column(pos, col.name, out)
    return sampled, BootstrapResult(table_name, n, n, seed, source.num_rows)
