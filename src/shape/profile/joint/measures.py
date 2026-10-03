"""Association measures on integer-coded columns (#47).

Every function takes columns already coded as non-negative integers (``-1`` marks a missing value
and is dropped pairwise) or as float arrays (NaN marks a missing value), so a pair costs a few
``bincount`` and sort calls on one bounded sample, never a pass over the whole table.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from shape.profile.sampling import KENDALL_SAMPLE_ROWS

DENSE_CELLS = 1 << 18  # a contingency table of more cells than this is counted by sorting


def contingency(a: np.ndarray, b: np.ndarray, ka: int, kb: int) -> np.ndarray:
    """The ``ka x kb`` count table of two coded columns (rows missing in either are dropped)."""
    m = (a >= 0) & (b >= 0)
    key = a[m].astype(np.int64) * kb + b[m]
    return np.bincount(key, minlength=ka * kb).reshape(ka, kb)


def _entropy(p: np.ndarray) -> float:
    p = p[p > 0]
    return float(-(p * np.log(p)).sum())


def mutual_information(t: np.ndarray) -> float:
    """Mutual information (nats) of a count table."""
    n = t.sum()
    if n <= 0:
        return 0.0
    pxy = t / n
    px = pxy.sum(axis=1, keepdims=True)
    py = pxy.sum(axis=0, keepdims=True)
    nz = pxy > 0
    with np.errstate(divide="ignore", invalid="ignore"):
        terms = pxy[nz] * np.log(pxy[nz] / (px @ py)[nz])
    return max(0.0, float(terms.sum()))


def cramers_v(t: np.ndarray) -> float:
    """Cramer's V with Bergsma's bias correction (0: independent, 1: one determines the other)."""
    n = float(t.sum())
    r, c = t.shape
    if n < 2 or r < 2 or c < 2:
        return 0.0
    row = t.sum(axis=1, keepdims=True)
    col = t.sum(axis=0, keepdims=True)
    expected = row @ col / n
    nz = expected > 0
    chi2 = float((((t - expected) ** 2)[nz] / expected[nz]).sum())
    phi2 = chi2 / n
    phi2c = max(0.0, phi2 - (r - 1) * (c - 1) / (n - 1))
    rc = r - (r - 1) ** 2 / (n - 1)
    cc = c - (c - 1) ** 2 / (n - 1)
    denom = min(cc - 1, rc - 1)
    return float(math.sqrt(phi2c / denom)) if denom > 0 else 0.0


def theil_u(t: np.ndarray) -> tuple[float, float]:
    """``(U(row | col), U(col | row))``: the share of one variable's entropy that the other
    explains. Asymmetric: U(city | zip) is 1 when the zip fixes the city."""
    n = t.sum()
    if n <= 0:
        return 0.0, 0.0
    mi = mutual_information(t)
    hr = _entropy(t.sum(axis=1) / n)
    hc = _entropy(t.sum(axis=0) / n)
    return (mi / hr if hr > 0 else 0.0), (mi / hc if hc > 0 else 0.0)


def correlation_ratio(codes: np.ndarray, values: np.ndarray, k: int) -> float:
    """Eta: how much of a numeric column's variance the categories explain (0 to 1)."""
    m = (codes >= 0) & ~np.isnan(values)
    c, y = codes[m], values[m]
    if len(y) < 3 or k < 2:
        return 0.0
    cnt = np.bincount(c, minlength=k).astype(np.float64)
    sums = np.bincount(c, weights=y, minlength=k)
    mean = y.mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        gm = np.where(cnt > 0, sums / cnt, 0.0)
    between = float((cnt * (gm - mean) ** 2).sum())
    total = float(((y - mean) ** 2).sum())
    return float(math.sqrt(between / total)) if total > 0 else 0.0


def ranks(x: np.ndarray) -> np.ndarray:
    """Average ranks (ties share the mean of their ranks)."""
    order = np.argsort(x, kind="stable")
    sx = x[order]
    first = np.concatenate(([True], sx[1:] != sx[:-1]))
    group = np.cumsum(first) - 1
    counts = np.bincount(group)
    ends = np.cumsum(counts)
    avg = ends - (counts - 1) / 2.0
    out = np.empty(len(x), dtype=np.float64)
    out[order] = avg[group]
    return out


def pearson(x: np.ndarray, y: np.ndarray) -> float | None:
    m = ~(np.isnan(x) | np.isnan(y))
    if m.sum() < 3:
        return None
    a, b = x[m], y[m]
    sa, sb = a.std(), b.std()
    if sa == 0 or sb == 0:
        return None
    return float(((a - a.mean()) * (b - b.mean())).mean() / (sa * sb))


def spearman(x: np.ndarray, y: np.ndarray) -> float | None:
    m = ~(np.isnan(x) | np.isnan(y))
    if m.sum() < 3:
        return None
    return pearson(ranks(x[m]), ranks(y[m]))


def kendall_tau(x: np.ndarray, y: np.ndarray, max_rows: int = KENDALL_SAMPLE_ROWS) -> float | None:
    """Kendall's tau-b on at most ``max_rows`` evenly spaced rows (the pair count is quadratic)."""
    m = ~(np.isnan(x) | np.isnan(y))
    a, b = x[m], y[m]
    if len(a) < 3:
        return None
    if len(a) > max_rows:
        idx = np.linspace(0, len(a) - 1, max_rows).astype(np.int64)
        a, b = a[idx], b[idx]
    sx = np.sign(a[:, None] - a[None, :])
    sy = np.sign(b[:, None] - b[None, :])
    s = float((sx * sy).sum()) / 2.0
    n0 = len(a) * (len(a) - 1) / 2.0
    n1 = float((sx != 0).sum()) / 2.0
    n2 = float((sy != 0).sum()) / 2.0
    denom = math.sqrt((n1) * (n2)) if n1 > 0 and n2 > 0 else 0.0
    del n0
    return s / denom if denom > 0 else None


def quantile_codes(x: np.ndarray, bins: int = 10) -> tuple[np.ndarray, int]:
    """A numeric column as quantile-bin codes (``-1`` where missing) and the bin count."""
    out = np.full(len(x), -1, dtype=np.int64)
    m = ~np.isnan(x)
    if not m.any():
        return out, 0
    v = x[m]
    edges = np.unique(np.quantile(v, np.linspace(0, 1, bins + 1)[1:-1]))
    out[m] = np.searchsorted(edges, v, side="right")
    return out, len(edges) + 1


@dataclass(frozen=True)
class FdStats:
    """How well one column determines another (see :func:`fd_stats`)."""

    rows: int
    groups: int
    repeat_groups: int  # determinant values seen in two or more rows
    violating_groups: int
    confidence: float
    baseline: float  # the share of the dependent's most frequent value
    support: float  # the share of rows in a repeated determinant group
    mode: npt.NDArray[np.int64]  # per determinant value: its modal dependent (-1: absent)
    group_ix: npt.NDArray[np.int64]
    distinct: npt.NDArray[np.int64]
    total: npt.NDArray[np.int64]
    most: npt.NDArray[np.int64]


def fd_stats(a: np.ndarray, b: np.ndarray, ka: int, kb: int) -> FdStats | None:
    """How well ``a`` determines ``b``: row-weighted share of rows whose ``b`` is the modal ``b``
    of their ``a`` group, plus the group evidence. None when fewer than two rows are present."""
    m = (a >= 0) & (b >= 0)
    n = int(m.sum())
    if n < 2:
        return None
    aa = a[m].astype(np.int64)
    bb = b[m].astype(np.int64)
    key = aa * kb + bb
    if ka * kb <= DENSE_CELLS:
        t = np.bincount(key, minlength=ka * kb).reshape(ka, kb)
        present = t.sum(axis=1)
        groups_ix = np.flatnonzero(present)
        mx = t.max(axis=1)[groups_ix]
        tot = present[groups_ix]
        distinct = (t[groups_ix] > 0).sum(axis=1)
        mode = t.argmax(axis=1)
        mode_full = np.full(ka, -1, dtype=np.int64)
        mode_full[groups_ix] = mode[groups_ix]
        bmode = float(np.bincount(bb, minlength=kb).max()) / n
    else:
        uk, cnt = np.unique(key, return_counts=True)
        ga = uk // kb
        gb = uk % kb
        starts = np.concatenate(([0], np.flatnonzero(np.diff(ga)) + 1))
        mx = np.maximum.reduceat(cnt, starts)
        tot = np.add.reduceat(cnt, starts)
        distinct = np.diff(np.concatenate((starts, [len(uk)])))
        groups_ix = ga[starts]
        # modal b of each group: the b of the first entry reaching the group's max count
        best = np.zeros(len(uk), dtype=bool)
        group_of_entry = np.repeat(np.arange(len(starts)), distinct)
        best = cnt == mx[group_of_entry]
        first_best = np.flatnonzero(best)
        pick = np.full(len(starts), -1, dtype=np.int64)
        pick[group_of_entry[first_best[::-1]]] = first_best[::-1]
        mode_full = np.full(ka, -1, dtype=np.int64)
        mode_full[groups_ix] = gb[pick]
        bmode = float(np.bincount(bb, minlength=kb).max()) / n
    in_repeats = tot >= 2
    return FdStats(
        rows=n,
        groups=int(len(groups_ix)),
        repeat_groups=int(in_repeats.sum()),
        violating_groups=int((distinct > 1).sum()),
        confidence=float(mx.sum()) / n,
        baseline=bmode,
        support=float(tot[in_repeats].sum()) / n,
        mode=mode_full,
        group_ix=groups_ix,
        distinct=distinct,
        total=tot,
        most=mx,
    )
