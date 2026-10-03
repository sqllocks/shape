"""Multivariate depth of the joint analysis (W3-08, issue #232): robust outliers, PCA, cohorts and
the mixed-type Gaussian copula.

Every function reads the joint analysis's bounded sample (float columns with NaN for a missing
value, or integer codes with ``-1``) and uses numpy only, so the results are the same in both kernel
modes and on every run: each random choice comes from a ``RandomState`` with a fixed seed. No row
value is stored: the entries hold counts, shares, summaries and a correlation matrix.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np

from . import measures as M

# --- chi-square (the distance threshold needs a quantile; scipy is not a dependency) -------------


def _gammainc(a: float, x: float) -> float:
    """The regularised lower incomplete gamma function ``P(a, x)``."""
    if x <= 0.0:
        return 0.0
    if x < a + 1.0:  # series
        term = total = 1.0 / a
        n = a
        for _ in range(1000):
            n += 1.0
            term *= x / n
            total += term
            if abs(term) < abs(total) * 1e-15:
                break
        return total * math.exp(-x + a * math.log(x) - math.lgamma(a))
    tiny = 1e-300  # continued fraction (modified Lentz)
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return 1.0 - math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def chi2_cdf(x: float, df: int) -> float:
    return _gammainc(df / 2.0, x / 2.0)


def chi2_ppf(q: float, df: int) -> float:
    """The ``q`` quantile of the chi-square distribution with ``df`` degrees of freedom."""
    if not 0.0 < q < 1.0:
        raise ValueError("q must be inside (0, 1)")
    hi = max(1.0, float(df))
    while chi2_cdf(hi, df) < q:
        hi *= 2.0
    lo = 0.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if chi2_cdf(mid, df) < q:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def norm_ppf(p: np.ndarray) -> np.ndarray:
    """The standard normal quantile function (Acklam's rational approximation, relative error
    about 1e-9, enough for rank scores)."""
    a = (-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00)  # fmt: skip
    b = (-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01)  # fmt: skip
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00)  # fmt: skip
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00)  # fmt: skip
    p = np.asarray(p, dtype=np.float64)
    out = np.empty_like(p)
    low = p < 0.02425
    high = p > 1 - 0.02425
    mid = ~(low | high)
    q = np.sqrt(-2.0 * np.log(p[low]))
    out[low] = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
        (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
    )
    q = np.sqrt(-2.0 * np.log(1.0 - p[high]))
    out[high] = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
        (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
    )
    q = p[mid] - 0.5
    r = q * q
    out[mid] = (
        (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
        * q
        / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    )
    return out


def _round(x: float, digits: int = 6) -> float:
    return round(float(x), digits)


# --- multivariate outliers: FAST-MCD ----------------------------------------------------------

MCD_SEED = 20_260_327
MCD_MIN_ROWS = 100
MCD_MIN_DISTINCT = 10  # a column with fewer values makes the h-subset's covariance singular
MCD_QUANTILE = 0.999
MCD_STARTS = 30  # elemental starts of FAST-MCD (on a subsample of at most MCD_SUBSAMPLE rows)
MCD_SUBSAMPLE = 600
MCD_KEEP = 5  # starts refined to convergence on the subsample
MCD_FULL = 2  # candidates refined to convergence on the whole sample
MCD_REWEIGHT = 0.975


def _independent_columns(x: np.ndarray, tol: float = 1e-6) -> list[int]:
    """The columns of standardised ``x`` kept so that none is a linear combination of the ones
    before it (a collinear column makes the covariance singular and every distance meaningless)."""
    z = (x - x.mean(axis=0)) / x.std(axis=0)
    keep: list[int] = []
    basis: list[np.ndarray] = []
    for j in range(x.shape[1]):
        v = z[:, j].copy()
        for q in basis:
            v -= q * float(q @ v)
        norm = float(np.linalg.norm(v))
        if norm / math.sqrt(len(v)) > tol:
            basis.append(v / norm)
            keep.append(j)
    return keep


def _dist2(x: np.ndarray, loc: np.ndarray, cov: np.ndarray) -> np.ndarray | None:
    try:
        chol = np.linalg.cholesky(cov)
    except np.linalg.LinAlgError:
        return None
    y = np.linalg.solve(chol, (x - loc).T)
    return np.asarray(np.einsum("ij,ij->j", y, y))


def _cstep(
    x: np.ndarray, h: int, loc: np.ndarray, cov: np.ndarray
) -> tuple[np.ndarray, np.ndarray, float] | None:
    d2 = _dist2(x, loc, cov)
    if d2 is None:
        return None
    # the h nearest rows; sorted so that the sums below run in the same order on every run
    sub = x[np.sort(np.argpartition(d2, h - 1)[:h])]
    loc = sub.mean(axis=0)
    c = (sub - loc).T @ (sub - loc) / h
    sign, logdet = np.linalg.slogdet(c)
    if sign <= 0 or not np.isfinite(logdet):
        return None
    return loc, c, float(logdet)


def _refine(
    x: np.ndarray, h: int, loc: np.ndarray, cov: np.ndarray, steps: int
) -> tuple[np.ndarray, np.ndarray, float] | None:
    sign, logdet = np.linalg.slogdet(cov)
    best: tuple[np.ndarray, np.ndarray, float] | None = None
    prev = math.inf
    for _ in range(steps):
        nxt = _cstep(x, h, loc, cov)
        if nxt is None:
            return best
        loc, cov, logdet = nxt
        best = nxt
        if prev - logdet <= 1e-12 * max(1.0, abs(logdet)):
            break
        prev = logdet
    return best


def _elemental_start(x: np.ndarray, rs: np.random.RandomState) -> tuple[np.ndarray, np.ndarray]:
    n, p = x.shape
    size = p + 1
    while size <= n:
        idx = rs.choice(n, size, replace=False)
        sub = x[idx]
        cov = np.cov(sub, rowvar=False, bias=True)
        sign, _ = np.linalg.slogdet(cov)
        if sign > 0:
            return sub.mean(axis=0), cov
        size += 1
    raise ValueError("no non-singular start")


def fast_mcd(x: np.ndarray, seed: int = MCD_SEED) -> tuple[np.ndarray, np.ndarray, int] | None:
    """The Minimum Covariance Determinant estimate of ``x`` (rows, columns) by FAST-MCD
    (Rousseeuw and Van Driessen): ``h = floor((n + p + 1) / 2)``, random elemental starts and
    concentration steps on a subsample, refined on the whole sample. Returns ``(location,
    covariance, h)`` with the covariance scaled to be consistent at the normal distribution, or None
    when every start is singular."""
    n, p = x.shape
    h = (n + p + 1) // 2
    rs = np.random.RandomState(seed)
    if n > MCD_SUBSAMPLE:
        sub = x[np.sort(rs.choice(n, MCD_SUBSAMPLE, replace=False))]
    else:
        sub = x
    hs = (len(sub) + p + 1) // 2
    starts: list[tuple[float, np.ndarray, np.ndarray]] = []
    for _ in range(MCD_STARTS):
        try:
            loc, cov = _elemental_start(sub, rs)
        except ValueError:
            continue
        got = _cstep(sub, hs, loc, cov)
        if got is None:
            continue
        got2 = _cstep(sub, hs, got[0], got[1])
        got = got2 or got
        starts.append((got[2], got[0], got[1]))
    if not starts:
        return None
    starts.sort(key=lambda s: s[0])
    refined: list[tuple[np.ndarray, np.ndarray, float]] = []
    for _, loc, cov in starts[:MCD_KEEP]:
        done = _refine(sub, hs, loc, cov, 50)
        if done is not None:
            refined.append(done)
    if not refined:
        return None
    refined.sort(key=lambda r: r[2])
    finals: list[tuple[np.ndarray, np.ndarray, float]] = []
    for loc, cov, _ in refined[:MCD_FULL]:
        done = _refine(x, h, loc, cov, 100)
        if done is not None:
            finals.append(done)
    if not finals:
        return None
    loc, cov, _ = min(finals, key=lambda r: r[2])
    alpha = h / n
    factor = alpha / chi2_cdf(chi2_ppf(alpha, p), p + 2)
    return loc, cov * factor, h


def _reweighted(
    x: np.ndarray, loc: np.ndarray, cov: np.ndarray
) -> tuple[np.ndarray, np.ndarray] | None:
    """One reweighting step: the mean and covariance of the rows within the 97.5% chi-square
    distance of the raw estimate, scaled for the rows the cut removes from a normal sample."""
    p = x.shape[1]
    d2 = _dist2(x, loc, cov)
    if d2 is None:
        return None
    cut = chi2_ppf(MCD_REWEIGHT, p)
    keep = d2 <= cut
    m = int(keep.sum())
    if m <= p + 1:
        return None
    sub = x[keep]
    loc2 = sub.mean(axis=0)
    cov2 = (sub - loc2).T @ (sub - loc2) / m
    cov2 = cov2 / (chi2_cdf(cut, p + 2) / MCD_REWEIGHT)
    if np.linalg.slogdet(cov2)[0] <= 0:
        return None
    return loc2, cov2


def multivariate_outliers(
    values: Sequence[np.ndarray], names: Sequence[str]
) -> dict[str, Any] | None:
    """``joint.multivariate_outliers``: rows whose robust squared Mahalanobis distance exceeds the
    0.999 chi-square quantile. ``values`` are the numeric columns of the sample (NaN: missing); rows
    with a missing value among the used columns are left out. None below two usable columns or
    ``MCD_MIN_ROWS`` complete rows."""
    cols = [i for i, v in enumerate(values) if len(np.unique(v[~np.isnan(v)])) >= MCD_MIN_DISTINCT]
    if len(cols) < 2:
        return None
    mat = np.column_stack([values[i] for i in cols])
    ok = ~np.isnan(mat).any(axis=1)
    mat = mat[ok]
    if len(mat) < MCD_MIN_ROWS:
        return None
    sd = mat.std(axis=0)
    if (sd <= 0).any():
        return None
    mat = (mat - np.median(mat, axis=0)) / sd
    keep = _independent_columns(mat)
    if len(keep) < 2:
        return None
    mat = mat[:, keep]
    used = [names[cols[j]] for j in keep]
    n, p = mat.shape
    fit = fast_mcd(mat)
    if fit is None:
        return None
    loc, cov, h = fit
    again = _reweighted(mat, loc, cov)
    if again is not None:
        loc, cov = again
    d2 = _dist2(mat, loc, cov)
    if d2 is None:
        return None
    threshold = chi2_ppf(MCD_QUANTILE, p)
    out = d2 > threshold
    count = int(out.sum())
    contributions = {name: 0.0 for name in used}
    if count:
        prec = np.linalg.inv(cov)
        z = mat[out] - loc
        resid = (z @ prec) ** 2 / np.diag(prec)  # squared standardised residual given the others
        share = resid / resid.sum(axis=1, keepdims=True)
        mean = share.mean(axis=0)
        contributions = {name: _round(float(m)) for name, m in zip(used, mean, strict=True)}
    return {
        "columns": used,
        "method": "mcd",
        "h": int(h),
        "rows": int(n),
        "threshold": _round(threshold),
        "outliers": count,
        "rate": _round(count / n),
        "contributions": contributions,
    }


# --- PCA ---------------------------------------------------------------------------------------

PCA_FULL = 0.95
PCA_EFFECTIVE = 0.90


def pca(values: Sequence[np.ndarray], names: Sequence[str]) -> dict[str, Any] | None:
    """``joint.pca``: the principal components of the standardised numeric columns (a missing
    value is the column mean). ``components`` are the loadings (one row per component, one entry per
    column) of the components that reach 95% of the variance, each with its largest absolute loading
    positive; ``effective_dimension`` is the number of components that reach 90%."""
    if len(values) < 2:
        return None
    mat = np.column_stack(list(values))
    n = len(mat)
    z = np.empty_like(mat)
    for j in range(mat.shape[1]):
        col = mat[:, j]
        ok = ~np.isnan(col)
        mean = col[ok].mean()
        sd = col[ok].std()
        if sd <= 0:
            return None
        z[:, j] = np.where(ok, (col - mean) / sd, 0.0)
    corr = z.T @ z / n
    vals, vecs = np.linalg.eigh(corr)
    order = np.argsort(-vals, kind="stable")
    vals = np.clip(vals[order], 0.0, None)
    vecs = vecs[:, order]
    ratio = vals / vals.sum()
    cum = np.cumsum(ratio)
    full = int(np.searchsorted(cum, PCA_FULL - 1e-12) + 1)
    eff = int(np.searchsorted(cum, PCA_EFFECTIVE - 1e-12) + 1)
    comps = []
    for c in range(full):
        v = vecs[:, c].copy()
        if v[int(np.argmax(np.abs(v)))] < 0:
            v = -v
        comps.append([_round(x) for x in v])
    return {
        "columns": list(names),
        "rows": int(n),
        "explained_variance_ratio": [_round(r) for r in ratio],
        "components": comps,
        "effective_dimension": eff,
    }


# --- cohorts: k-means with k-means++ seeding, k by silhouette -------------------------------------

COHORT_SEED = 20_260_328
COHORT_MIN_ROWS = 100
COHORT_K = range(2, 9)
COHORT_SUBSAMPLE = 2_000
COHORT_MIN_SILHOUETTE = 0.25
COHORT_NULL_MARGIN = 0.10  # the silhouette must beat a single Gaussian of the same covariance
COHORT_MIN_STABILITY = 0.90  # adjusted Rand index of the partitions two halves of the sample give
COHORT_STABILITY_SPLITS = 3
COHORT_MAX_LEVELS = 20
COHORT_INITS = 2


def _kmeans_pp(x: np.ndarray, k: int, rs: np.random.RandomState) -> np.ndarray:
    n = len(x)
    centers = np.empty((k, x.shape[1]))
    centers[0] = x[rs.randint(n)]
    d2 = ((x - centers[0]) ** 2).sum(axis=1)
    for i in range(1, k):
        total = d2.sum()
        pick = (
            rs.randint(n)
            if total <= 0
            else int(np.searchsorted(np.cumsum(d2), rs.random_sample() * total))
        )
        pick = min(pick, n - 1)
        centers[i] = x[pick]
        d2 = np.minimum(d2, ((x - centers[i]) ** 2).sum(axis=1))
    return centers


def _assign(x: np.ndarray, centers: np.ndarray) -> tuple[np.ndarray, float]:
    labels = np.empty(len(x), dtype=np.int64)
    inertia = 0.0
    csq = (centers**2).sum(axis=1)
    for start in range(0, len(x), 4096):
        block = x[start : start + 4096]
        d2 = (block**2).sum(axis=1)[:, None] - 2.0 * block @ centers.T + csq[None, :]
        lab = np.argmin(d2, axis=1)
        labels[start : start + 4096] = lab
        inertia += float(np.maximum(d2[np.arange(len(block)), lab], 0.0).sum())
    return labels, inertia


def _lloyd(x: np.ndarray, centers: np.ndarray, iters: int) -> tuple[np.ndarray, np.ndarray, float]:
    k, n = len(centers), len(x)
    labels, inertia = _assign(x, centers)
    for _ in range(iters):
        onehot = np.zeros((n, k))
        onehot[np.arange(n), labels] = 1.0
        counts = onehot.sum(axis=0)
        new = (onehot.T @ x) / np.maximum(counts, 1.0)[:, None]
        for j in np.flatnonzero(
            counts == 0
        ):  # an empty cluster takes the row farthest from its centre
            d = ((x - centers[labels]) ** 2).sum(axis=1)
            far = int(np.argmax(d))
            new[j] = x[far]
            labels[far] = j
        moved, inertia = _assign(x, new)
        centers = new
        if np.array_equal(moved, labels):
            break
        labels = moved
    return centers, labels, inertia


def _kmeans(x: np.ndarray, k: int, seed: int) -> tuple[np.ndarray, np.ndarray, float]:
    best: tuple[np.ndarray, np.ndarray, float] | None = None
    for init in range(COHORT_INITS):
        rs = np.random.RandomState(seed + 1000 * k + init)
        got = _lloyd(x, _kmeans_pp(x, k, rs), 30)
        if best is None or got[2] < best[2] - 1e-12:
            best = got
    assert best is not None
    return best


def _distances(x: np.ndarray) -> np.ndarray:
    """The pairwise Euclidean distances of the rows of ``x`` as one float32 matrix, built in place
    (it is the largest array of the cohort search: 16 MB for 2,000 rows)."""
    x32 = x.astype(np.float32)
    sq = (x32**2).sum(axis=1)
    d = x32 @ x32.T
    d *= -2.0
    d += sq[:, None]
    d += sq[None, :]
    np.maximum(d, 0.0, out=d)
    np.fill_diagonal(d, 0.0)
    np.sqrt(d, out=d)
    return d


def _silhouette(dist: np.ndarray, labels: np.ndarray, k: int) -> float:
    n = len(labels)
    onehot = np.zeros((n, k), dtype=np.float32)
    onehot[np.arange(n), labels] = 1.0
    sums = (dist @ onehot).astype(np.float64)
    counts = onehot.sum(axis=0)
    own = counts[labels]
    a = sums[np.arange(n), labels] / np.maximum(own - 1.0, 1.0)
    other = sums / np.maximum(counts, 1.0)[None, :]
    other[np.arange(n), labels] = np.inf
    other[:, counts == 0] = np.inf
    b = other.min(axis=1)
    s = (b - a) / np.maximum(np.maximum(a, b), 1e-300)
    s[own <= 1] = 0.0
    return float(s.mean())


def _search_k(x: np.ndarray, seed: int) -> tuple[int, float, np.ndarray]:
    """The ``k`` with the best silhouette on ``x`` (ties to the smaller), its silhouette and
    centres."""
    dist = _distances(x)
    best: tuple[int, float, np.ndarray] | None = None
    for k in COHORT_K:
        if k >= len(x):
            break
        centers, labels, _ = _kmeans(x, k, seed)
        if len(np.unique(labels)) < k:
            continue
        s = _silhouette(dist, labels, k)
        if best is None or s > best[1] + 1e-9:
            best = (k, s, centers)
    if best is None:
        return 0, -1.0, np.empty((0, x.shape[1]))
    return best


def _adjusted_rand(a: np.ndarray, b: np.ndarray) -> float:
    ka, kb = int(a.max()) + 1, int(b.max()) + 1
    table = np.bincount(a * kb + b, minlength=ka * kb).reshape(ka, kb).astype(np.float64)

    def pairs(x: np.ndarray) -> float:
        return float((x * (x - 1.0) / 2.0).sum())

    n = float(table.sum())
    joint, rows, cols = pairs(table), pairs(table.sum(axis=1)), pairs(table.sum(axis=0))
    expected = rows * cols / (n * (n - 1.0) / 2.0)
    best = (rows + cols) / 2.0
    return 1.0 if best == expected else (joint - expected) / (best - expected)


def _stability(x: np.ndarray, k: int, centers: np.ndarray, seed: int) -> float:
    """How well the ``k`` clusters of ``x`` reproduce: the lowest adjusted Rand index between their
    partition of ``x`` and the one that centres fitted on random halves of ``x`` give. Separated
    clusters give 1; a split of one continuous cloud, which moves with the sample, gives much
    less."""
    rs = np.random.RandomState(seed ^ 0x57AB)
    reference, _ = _assign(x, centers)
    worst = 1.0
    for split in range(COHORT_STABILITY_SPLITS):
        half = x[np.sort(rs.choice(len(x), len(x) // 2, replace=False))]
        fitted, _, _ = _kmeans(half, k, seed + 31 * (split + 1))
        labels, _ = _assign(x, fitted)
        worst = min(worst, _adjusted_rand(reference, labels))
    return worst


def _null_silhouette(x: np.ndarray, seed: int) -> float:
    """The best silhouette of a single Gaussian with the covariance of ``x``: a low-dimensional
    unclustered cloud already scores above 0.25, so the data must beat this to count as cohorts."""
    rs = np.random.RandomState(seed ^ 0x5EED)
    mean = x.mean(axis=0)
    cov = np.cov(x, rowvar=False, bias=True) if x.shape[1] > 1 else np.array([[x.var()]])
    vals, vecs = np.linalg.eigh(cov)
    root = vecs * np.sqrt(np.clip(vals, 0.0, None))
    fake = rs.standard_normal(x.shape) @ root.T + mean
    return _search_k(fake, seed + 17)[1]


def cohorts(
    numeric: Sequence[tuple[str, np.ndarray]],
    categorical: Sequence[tuple[str, np.ndarray, Sequence[str]]],
    rows: int,
) -> dict[str, Any] | None:
    """``joint.cohorts`` from the standardised numeric columns and the one-hot encoded categorical
    columns (name, codes with ``-1`` missing, labels) of the sample. None below ``COHORT_MIN_ROWS``
    rows or two columns."""
    if rows < COHORT_MIN_ROWS or len(numeric) + len(categorical) < 2:
        return None
    blocks: list[np.ndarray] = []
    for _, v in numeric:
        ok = ~np.isnan(v)
        sd = float(v[ok].std()) if ok.any() else 0.0
        blocks.append(
            np.where(ok, (v - float(v[ok].mean())) / sd, 0.0)[:, None]
            if sd > 0
            else np.zeros((rows, 1))
        )
    for _, codes, levels in categorical:
        hot = np.zeros((rows, len(levels)))
        present = codes >= 0
        hot[np.flatnonzero(present), codes[present]] = 1.0
        blocks.append(hot)
    x = np.hstack(blocks)
    rs = np.random.RandomState(COHORT_SEED)
    sub = (
        x
        if rows <= COHORT_SUBSAMPLE
        else x[np.sort(rs.choice(rows, COHORT_SUBSAMPLE, replace=False))]
    )
    k, sil, centers = _search_k(sub, COHORT_SEED)
    if k == 0:
        return {"found": False, "silhouette": 0.0}
    if sil < COHORT_MIN_SILHOUETTE or sil - _null_silhouette(sub, COHORT_SEED) < COHORT_NULL_MARGIN:
        return {"found": False, "silhouette": _round(sil)}
    if _stability(sub, k, centers, COHORT_SEED) < COHORT_MIN_STABILITY:
        return {"found": False, "silhouette": _round(sil)}
    centers, labels, _ = _lloyd(x, centers, 30)
    counts = np.bincount(labels, minlength=k)
    order = sorted(range(k), key=lambda j: (-int(counts[j]), tuple(np.round(centers[j], 6))))
    out: list[dict[str, Any]] = []
    for j in order:
        if not counts[j]:
            continue
        members = labels == j
        summary: dict[str, Any] = {}
        for name, v in numeric:
            vals = v[members]
            vals = vals[~np.isnan(vals)]
            summary[name] = (
                {"mean": _round(vals.mean()), "std": _round(vals.std())}
                if len(vals)
                else {"mean": None, "std": None}
            )
        for name, codes, labs in categorical:
            c = codes[members]
            c = c[c >= 0]
            if len(c) == 0:
                summary[name] = {"mode": None, "share": None}
                continue
            tally = np.bincount(c, minlength=len(labs))
            top = max(range(len(labs)), key=lambda i: (int(tally[i]), -i))  # ties: first label
            summary[name] = {"mode": labs[top], "share": _round(tally[top] / len(c))}
        out.append({"share": _round(counts[j] / rows), "rows": int(counts[j]), "summary": summary})
    return {
        "found": True,
        "k": len(out),
        "silhouette": _round(sil),
        "rows": int(rows),
        "numeric": [n for n, _ in numeric],
        "categorical": [n for n, _, _ in categorical],
        "cohorts": out,
    }


# --- the mixed-type Gaussian copula ---------------------------------------------------------------

COPULA_FORMAT = "shape.copula"
COPULA_VERSION = 1
COPULA_MIN_ROWS = 100
COPULA_MAX_LEVELS = 200  # categories stored (a column with more levels is left out)
COPULA_STEP_MAX = 200  # a numeric column with up to this many values is a step function
COPULA_MIN_EIGENVALUE = 1e-4
COPULA_TERMS = 80  # terms of the Hermite series of the bridge


def normal_scores(x: np.ndarray) -> np.ndarray:
    """Normal scores of the non-missing values by mid-rank (NaN stays NaN)."""
    out = np.full(len(x), np.nan)
    ok = ~np.isnan(x)
    m = int(ok.sum())
    if m:
        out[ok] = norm_ppf((M.ranks(x[ok]) - 0.5) / m)
    return out


def positive_definite(corr: np.ndarray) -> np.ndarray:
    """The correlation matrix with its eigenvalues raised to at least ``COPULA_MIN_EIGENVALUE`` and
    its diagonal restored to 1."""
    vals, vecs = np.linalg.eigh((corr + corr.T) / 2.0)
    fixed = (vecs * np.clip(vals, COPULA_MIN_EIGENVALUE, None)) @ vecs.T
    scale = 1.0 / np.sqrt(np.diag(fixed))
    out = fixed * scale[:, None] * scale[None, :]
    np.fill_diagonal(out, 1.0)
    return np.asarray(out)


def _phi(z: np.ndarray) -> np.ndarray:
    return np.asarray(np.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi))


def _hermite_coefficients(counts: np.ndarray | None) -> tuple[np.ndarray, float]:
    """The normalised Hermite coefficients ``E[g(Z) He_m(Z)] / sqrt(m!)`` (``m = 1 ..``) and the
    standard deviation of ``g(Z)``, where ``g`` maps a latent normal to the normal score of the
    mid-rank of its level. Levels are in order with ``counts`` rows each; ``None`` is a continuous
    column, whose score is the latent normal itself."""
    coef = np.zeros(COPULA_TERMS)
    if counts is None:
        coef[0] = 1.0
        return coef, 1.0
    p = counts / counts.sum()
    cum = np.concatenate(([0.0], np.cumsum(p)))
    cum[-1] = 1.0
    score = norm_ppf(np.clip((cum[:-1] + cum[1:]) / 2.0, 1e-12, 1 - 1e-12))
    inner = np.clip(cum[1:-1], 1e-12, 1 - 1e-12)
    t = norm_ppf(inner)  # the K - 1 thresholds
    dens = _phi(t)
    # h_m at the thresholds: h_0 = 1, h_1 = z, h_{m+1} = (z h_m - sqrt(m) h_{m-1}) / sqrt(m + 1)
    h_prev = np.ones_like(t)
    h_cur = t.copy()
    mean = float((p * score).sum())
    sd = math.sqrt(max(float((p * score**2).sum()) - mean * mean, 0.0))
    for m in range(1, COPULA_TERMS + 1):
        # E[g h_m] = sum_k s_k * (h_{m-1}(t_{k-1}) phi(t_{k-1}) - h_{m-1}(t_k) phi(t_k)) / sqrt(m)
        edge = np.concatenate(([0.0], h_prev * dens, [0.0]))
        coef[m - 1] = float((score * (edge[:-1] - edge[1:])).sum()) / math.sqrt(m)
        h_prev, h_cur = h_cur, (t * h_cur - math.sqrt(m) * h_prev) / math.sqrt(m + 1)
    return coef, sd


def _latent_correlation(
    observed: float, a: tuple[np.ndarray, float], b: tuple[np.ndarray, float]
) -> float:
    """The latent correlation whose normal scores correlate as ``observed``: the bridge between the
    correlation of the (stepped) scores and of the latent normals, by Mehler's expansion."""
    ca, sa = a
    cb, sb = b
    if sa <= 0 or sb <= 0 or abs(observed) < 1e-12:
        return 0.0
    prod = ca * cb / (sa * sb)
    powers = np.arange(1, COPULA_TERMS + 1)

    def forward(rho: float) -> float:
        return float((prod * rho**powers).sum())

    limit = 0.9999
    if abs(observed) >= abs(forward(math.copysign(limit, observed))):
        return math.copysign(limit, observed)
    lo, hi = (-limit, limit)
    for _ in range(26):  # the interval is 2 wide: 3e-8
        mid = 0.5 * (lo + hi)
        if forward(mid) < observed:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def copula(
    numeric: Sequence[tuple[str, np.ndarray]],
    categorical: Sequence[tuple[str, np.ndarray, Sequence[str], np.ndarray]],
    rows: int,
) -> dict[str, Any] | None:
    """``joint.copula``: the latent correlation matrix of a Gaussian copula over the numeric and the
    categorical columns. The correlation of the normal scores (mid-ranks; a category's rank is the
    one of its place in the stored order, by frequency, ties by value) is bridged to the latent
    correlation, so that reordering columns by a correlated latent normal gives back the
    normal-score correlations of the profile, then made positive definite. ``categorical`` entries
    are ``(name, codes, labels, counts)``. None below ``COPULA_MIN_ROWS`` rows or two columns."""
    if rows < COPULA_MIN_ROWS or len(numeric) + len(categorical) < 2:
        return None
    names: list[str] = []
    scores: list[np.ndarray] = []
    steps: list[tuple[np.ndarray, float]] = []
    categories: dict[str, list[str]] = {}
    for name, v in numeric:
        names.append(name)
        scores.append(normal_scores(v))
        _, counts = np.unique(v[~np.isnan(v)], return_counts=True)
        steps.append(_hermite_coefficients(counts if len(counts) <= COPULA_STEP_MAX else None))
    for name, codes, labels, counts in categorical:
        order = sorted(range(len(labels)), key=lambda i: (-int(counts[i]), labels[i]))
        position = np.empty(len(labels), dtype=np.float64)
        position[order] = np.arange(len(labels), dtype=np.float64)
        v = np.where(codes >= 0, position[np.maximum(codes, 0)], np.nan)
        names.append(name)
        scores.append(normal_scores(v))
        steps.append(
            _hermite_coefficients(np.asarray([counts[i] for i in order], dtype=np.float64))
        )
        categories[name] = [labels[i] for i in order]
    k = len(names)
    corr = np.eye(k)
    for i in range(k):
        for j in range(i + 1, k):
            both = ~(np.isnan(scores[i]) | np.isnan(scores[j]))
            if int(both.sum()) < 10:
                continue
            a, b = scores[i][both], scores[j][both]
            sa, sb = a.std(), b.std()
            if sa > 0 and sb > 0:
                observed = float(((a - a.mean()) * (b - b.mean())).mean() / (sa * sb))
                corr[i, j] = corr[j, i] = _latent_correlation(observed, steps[i], steps[j])
    corr = positive_definite(corr)
    return {
        "format": COPULA_FORMAT,
        "version": COPULA_VERSION,
        "rows": int(rows),
        "columns": names,
        "numeric": [n for n, _ in numeric],
        "categorical": [c[0] for c in categorical],
        "categories": categories,
        "correlation": [[_round(x) for x in row] for row in corr],
    }
