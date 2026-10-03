"""Drift of the multivariate entries of the joint analysis (W3-08): a rise in the share of
multivariate outliers, a change in the structure of the numeric columns (PCA) and a shift in the
cohorts. Each reads the table's ``joint`` entry of both profiles and reports nothing when either
side lacks the entry (an older profile, a table too small for it, a stream window).

Every one is held to sampling noise: two samples of one distribution do not drift.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

import numpy as np

from .joint import _label, _record, _skipped

if TYPE_CHECKING:
    from .engine import Policy, TableView

STRUCTURE_DIMENSION_STEP = 2  # the effective dimension changed by this many components or more
STRUCTURE_NOISE_SIGMAS = 3.0
COHORT_NOISE_SIGMAS = 3.0
# the robust fit moves with the sample, so the rate of a skewed table varies up to 1.6 times its
# binomial spread (measured on gamma and log-normal columns): six binomial standard errors
OUTLIER_NOISE_SIGMAS = 6.0


def outlier_rate_noise(rate_a: float, n_a: int, rate_b: float, n_b: int) -> float:
    """Six binomial standard errors of the difference of two rates, at the pooled rate."""
    pooled = (rate_a * n_a + rate_b * n_b) / max(n_a + n_b, 1)
    var = max(pooled * (1.0 - pooled), 0.0) * (1.0 / max(n_a, 1) + 1.0 / max(n_b, 1))
    return OUTLIER_NOISE_SIGMAS * math.sqrt(var)


def _outliers(
    table: str | None, bt: TableView, ct: TableView, th: Mapping[str, Any], policy: Policy
) -> list[dict[str, Any]]:
    b = (bt.joint or {}).get("multivariate_outliers")
    c = (ct.joint or {}).get("multivariate_outliers")
    if not b or not c or b.get("columns") != c.get("columns"):
        return []
    cols = list(c["columns"])
    if _skipped(policy, table, cols):
        return []
    rb, rc = float(b["rate"]), float(c["rate"])
    rise = rc - rb
    floor = max(th["multivariate_outlier_rate"], outlier_rate_noise(rb, b["rows"], rc, c["rows"]))
    if rise <= floor:
        return []
    top = max(c["contributions"].items(), key=lambda kv: kv[1])[0] if c["contributions"] else None
    msg = f"multivariate outliers went from {rb:.1%} to {rc:.1%} of rows ({', '.join(cols)})"
    if top is not None and c["contributions"][top] > 0:
        msg += f"; mostly {top} ({c['contributions'][top]:.0%} of their distance)"
    return [
        _record(
            _label(table, "(rows)"),
            "multivariate_outlier_rate_change",
            rb,
            rc,
            rise,
            {
                "columns": cols,
                "baseline_rate": rb,
                "current_rate": rc,
                "contributions": c["contributions"],
                "rows": [b["rows"], c["rows"]],
            },
            msg,
        )
    ]


def _loadings(entry: Mapping[str, Any], columns: Sequence[str], count: int) -> np.ndarray:
    """The first ``count`` components of a PCA entry over ``columns``, orthonormalised (the loadings
    cut to the shared columns are no longer unit vectors); one column per direction."""
    index = {c: i for i, c in enumerate(entry["columns"])}
    rows = [[comp[index[c]] for c in columns] for comp in entry["components"][:count]]
    a = np.array(rows, dtype=np.float64).T
    u, s, _ = np.linalg.svd(a, full_matrices=False)
    return np.asarray(u[:, s > 1e-8])


def principal_angle(
    a: Mapping[str, Any], b: Mapping[str, Any], dimension: int
) -> tuple[float, list[str]] | None:
    """The largest principal angle in degrees between the leading ``dimension`` components of two
    PCA entries over their shared columns, with those columns. None when fewer than two columns are
    shared or a profile stores fewer components than needed."""
    shared = [c for c in a["columns"] if c in set(b["columns"])]
    if len(shared) < 2 or dimension < 1 or dimension >= len(shared):
        return None
    qa = _loadings(a, shared, dimension)
    qb = _loadings(b, shared, min(dimension, len(b["components"])))
    if qa.shape[1] == 0 or qb.shape[1] == 0:
        return None
    sv = np.linalg.svd(qa.T @ qb, compute_uv=False)
    angle = math.degrees(math.acos(min(1.0, max(0.0, float(sv.min())))))
    return angle, shared


def subspace_noise_angle(ratios: Sequence[float], dimension: int, n_a: int, n_b: int) -> float:
    """The sampling noise, in degrees, of the leading ``dimension``-dimensional subspace of two
    samples of one distribution with the spectrum ``ratios`` (the explained variance ratios): three
    times the asymptotic Frobenius sine of the angle, ``sqrt(sum l_i l_j / (l_i - l_j)^2 / n)`` over
    the pairs across the cut. The gap of each pair is first reduced by three standard errors of the
    two eigenvalues (``l sqrt(2 / n)`` each): a sample's gaps are wider than the true ones, and a
    gap inside that error leaves the subspace arbitrary, so the noise is 90 degrees."""
    lam = [float(r) for r in ratios]
    n = 1.0 / (1.0 / max(n_a, 1) + 1.0 / max(n_b, 1))  # the sample size of the difference
    err = math.sqrt(2.0 / max(n, 1.0))
    total = 0.0
    for i in range(dimension):
        for j in range(dimension, len(lam)):
            gap = lam[i] - lam[j] - STRUCTURE_NOISE_SIGMAS * err * (lam[i] + lam[j])
            if gap <= 1e-12:
                return 90.0
            total += lam[i] * lam[j] / (gap * gap)
    sine = math.sqrt(total / max(n, 1.0))
    return math.degrees(math.asin(min(1.0, STRUCTURE_NOISE_SIGMAS * sine)))


def _structure(
    table: str | None, bt: TableView, ct: TableView, th: Mapping[str, Any], policy: Policy
) -> list[dict[str, Any]]:
    b = (bt.joint or {}).get("pca")
    c = (ct.joint or {}).get("pca")
    if not b or not c:
        return []
    shared = [x for x in b["columns"] if x in set(c["columns"])]
    if _skipped(policy, table, shared):
        return []
    db, dc = int(b["effective_dimension"]), int(c["effective_dimension"])
    steps = abs(dc - db)
    found = None
    if b["columns"] == c["columns"] and steps >= STRUCTURE_DIMENSION_STEP:
        found = ("dimension", float(steps))
    angle = principal_angle(b, c, db)
    limit = float(th["structure_angle"])
    noise = max(
        subspace_noise_angle(b["explained_variance_ratio"], db, b["rows"], c["rows"]),
        subspace_noise_angle(c["explained_variance_ratio"], db, b["rows"], c["rows"]),
    )
    degrees = None if angle is None else angle[0]
    if found is None and degrees is not None and degrees > max(limit, noise):
        found = ("subspace", degrees)
    if found is None:
        return []
    if found[0] == "dimension":
        msg = f"the numeric columns have {dc} effective dimensions, they had {db}"
        score = steps / max(len(b["columns"]), 1)
    else:
        msg = (
            f"the leading {db}-dimensional subspace of the numeric columns turned by "
            f"{degrees:.0f} degrees (effective dimension {db} -> {dc})"
        )
        score = (degrees or 0.0) / 90.0
    return [
        _record(
            _label(table, "(numeric columns)"),
            "structure_change",
            db,
            dc,
            score,
            {
                "reason": found[0],
                "baseline_dimension": db,
                "current_dimension": dc,
                "angle_degrees": None if degrees is None else round(degrees, 2),
                "noise_degrees": round(noise, 2),
                "columns": shared,
            },
            msg,
        )
    ]


def _cohort_scale(cohorts: Sequence[Mapping[str, Any]], column: str) -> tuple[float, float] | None:
    """The overall mean and standard deviation of a numeric column from the cohorts' summaries."""
    mean = second = 0.0
    for k in cohorts:
        s = k["summary"].get(column)
        if not s or s.get("mean") is None:
            return None
        mean += k["share"] * s["mean"]
        second += k["share"] * (s["std"] ** 2 + s["mean"] ** 2)
    return mean, math.sqrt(max(second - mean * mean, 0.0))


def _cohort_distance(
    a: Mapping[str, Any],
    b: Mapping[str, Any],
    numeric: Sequence[tuple[str, float]],
    categorical: Sequence[str],
) -> float:
    d2 = 0.0
    for col, scale in numeric:
        d = (a["summary"][col]["mean"] - b["summary"][col]["mean"]) / scale
        d2 += d * d
    for col in categorical:
        if a["summary"][col]["mode"] != b["summary"][col]["mode"]:
            d2 += 2.0  # two one-hot vectors that differ
    return d2


def match_cohorts(
    base: Sequence[Mapping[str, Any]], cur: Sequence[Mapping[str, Any]], columns: Sequence[str]
) -> list[int] | None:
    """For each baseline cohort, the index of the nearest current cohort (by the cohorts' summaries
    over ``columns``, numeric means in units of the baseline's overall standard deviation)."""
    numeric: list[tuple[str, float]] = []
    categorical: list[str] = []
    for col in columns:
        if "mean" in base[0]["summary"][col]:
            scale = _cohort_scale(base, col)
            if scale is None:
                return None
            numeric.append((col, scale[1] if scale[1] > 0 else 1.0))
        else:
            categorical.append(col)
    return [
        min(range(len(cur)), key=lambda j: (_cohort_distance(k, cur[j], numeric, categorical), j))
        for k in base
    ]


def _cohorts(
    table: str | None, bt: TableView, ct: TableView, th: Mapping[str, Any], policy: Policy
) -> list[dict[str, Any]]:
    b = (bt.joint or {}).get("cohorts")
    c = (ct.joint or {}).get("cohorts")
    if not b or not c or not b.get("found") or not c.get("found"):
        return []
    bcols = list(b["numeric"]) + list(b["categorical"])
    ccols = set(c["numeric"]) | set(c["categorical"])
    shared = [
        x
        for x in bcols
        if x in ccols
        and ("mean" in b["cohorts"][0]["summary"][x]) == ("mean" in c["cohorts"][0]["summary"][x])
    ]
    if len(shared) < 2 or _skipped(policy, table, shared):
        return []
    match = match_cohorts(b["cohorts"], c["cohorts"], shared)
    if match is None:
        return []
    mapped = [0.0] * len(c["cohorts"])
    for k, j in zip(b["cohorts"], match, strict=True):
        mapped[j] += float(k["share"])
    current = [float(k["share"]) for k in c["cohorts"]]
    tvd = 0.5 * sum(abs(x - y) for x, y in zip(mapped, current, strict=True))
    nb, nc = int(b["rows"]), int(c["rows"])
    noise = 0.5 * sum(
        COHORT_NOISE_SIGMAS
        * math.sqrt(max(p * (1.0 - p), 0.0) * (1.0 / max(nb, 1) + 1.0 / max(nc, 1)))
        for p in ((x + y) / 2.0 for x, y in zip(mapped, current, strict=True))
    )
    if tvd <= max(th["cohort_tvd"], noise):
        return []
    shifts = sorted(
        (
            {
                "cohort": j,
                "baseline_share": round(mapped[j], 6),
                "current_share": round(current[j], 6),
            }
            for j in range(len(current))
        ),
        key=lambda s: -abs(s["current_share"] - s["baseline_share"]),
    )
    top = shifts[0]
    msg = (
        f"the cohorts moved by a total variation distance of {tvd:.2f}: the largest, cohort "
        f"{top['cohort']}, went from {top['baseline_share']:.1%} to "
        f"{top['current_share']:.1%} of rows"
    )
    return [
        _record(
            _label(table, "(cohorts)"),
            "cohort_shift",
            [round(x, 6) for x in mapped],
            [round(x, 6) for x in current],
            tvd,
            {
                "total_variation_distance": round(tvd, 6),
                "noise": round(noise, 6),
                "matched": match,
                "shares": shifts,
                "columns": shared,
            },
            msg,
        )
    ]


def diff_multivariate(
    table: str | None, bt: TableView, ct: TableView, th: Mapping[str, Any], policy: Policy
) -> list[dict[str, Any]]:
    return (
        _outliers(table, bt, ct, th, policy)
        + _structure(table, bt, ct, th, policy)
        + _cohorts(table, bt, ct, th, policy)
    )
