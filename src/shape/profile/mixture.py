"""Gaussian mixtures for a numeric column (W7-03): ``k`` = 1 to 4 components fitted by EM and
chosen by BIC. ``docs/PROFILING_NOTES.md`` gives the formulas, the minimum sample and when it is
not computed.

Computed once in Python over numpy, from a deterministic sample, so the result is the same under
either kernel. The existing ``distribution`` fields and ``distribution_candidates`` are untouched.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from .univariate import sample_values

MIN_VALUES = 200
"""Fewest finite values for a mixture."""
MAX_K = 4
MAX_ITER = 200
TOLERANCE = 1e-6
"""EM stops when the mean log-likelihood per value improves by less than this."""
MIN_WEIGHT = 0.05
"""Every component of a ``multimodal`` mixture weighs at least this."""
SD_FLOOR = 1e-3
"""A component's standard deviation never falls below this share of the column's."""
SAMPLE_CAP = 4_000
"""Most values read (the sampling rule of :func:`shape.profile.univariate.sample_values`)."""
_LOG_2PI = math.log(2.0 * math.pi)


def _estep(
    z: np.ndarray, w: np.ndarray, mu: np.ndarray, sd: np.ndarray
) -> tuple[float, np.ndarray]:
    """The log-likelihood of the values and each component's responsibility for each value."""
    lp = -0.5 * ((z[:, None] - mu) / sd) ** 2 - np.log(sd) - 0.5 * _LOG_2PI + np.log(w)
    m = lp.max(axis=1)
    e = np.exp(lp - m[:, None])
    total = e.sum(axis=1)
    return float((m + np.log(total)).sum()), e / total[:, None]


def _fit(z: np.ndarray, k: int) -> tuple[float, np.ndarray, np.ndarray, np.ndarray]:
    """EM on the standardised sample ``z`` (sorted), from a quantile initialisation: ``k`` runs of
    equal size give each component's weight, mean and spread. Returns the log-likelihood and the
    weights, means and standard deviations, in order of mean."""
    n = len(z)
    z2 = z * z
    parts = np.array_split(z, k)
    w = np.array([len(p) / n for p in parts])
    mu = np.array([p.mean() for p in parts])
    sd = np.maximum([p.std() for p in parts], SD_FLOOR)
    prev = -math.inf
    for _ in range(MAX_ITER):
        ll, r = _estep(z, w, mu, sd)
        if abs(ll - prev) <= TOLERANCE * n:
            break
        prev = ll
        nk = np.maximum(r.sum(axis=0), 1e-12)
        w = nk / n
        mu = (r.T @ z) / nk
        sd = np.sqrt(np.maximum((r.T @ z2) / nk - mu * mu, 0.0))
        sd = np.maximum(sd, SD_FLOOR)
    ll, _ = _estep(z, w, mu, sd)
    order = np.argsort(mu, kind="stable")
    return ll, w[order], mu[order], sd[order]


def mixture(x: np.ndarray) -> dict[str, Any] | None:
    """The mixture entry of a column: ``{k, components: [{weight, mean, sd}], bic_by_k,
    multimodal}``, or ``None`` below :data:`MIN_VALUES` finite values or for a constant column."""
    x = np.asarray(x, dtype=np.float64)
    x = x[np.isfinite(x)]
    if len(x) < MIN_VALUES:
        return None
    s = sample_values(x, SAMPLE_CAP)
    centre, scale = float(s.mean()), float(s.std())
    if not scale > 0.0:
        return None
    z = np.sort((s - centre) / scale)
    n = len(z)
    best: tuple[float, int, np.ndarray, np.ndarray, np.ndarray] | None = None
    bic_by_k: dict[str, float] = {}
    for k in range(1, MAX_K + 1):
        ll, w, mu, sd = _fit(z, k)
        ll -= n * math.log(scale)  # the log-likelihood of the values, not of their z-scores
        bic = (3 * k - 1) * math.log(n) - 2.0 * ll
        bic_by_k[str(k)] = round(bic, 3)
        if best is None or bic < best[0]:
            best = (bic, k, w, mu, sd)
    assert best is not None
    _, k, w, mu, sd = best
    components = [
        {
            "weight": round(float(wi), 6),
            "mean": float(f"{centre + scale * mi:.9g}"),
            "sd": float(f"{scale * si:.9g}"),
        }
        for wi, mi, si in zip(w, mu, sd, strict=True)
    ]
    return {
        "k": k,
        "components": components,
        "bic_by_k": bic_by_k,
        "multimodal": bool(k >= 2 and all(c["weight"] >= MIN_WEIGHT for c in components)),
    }


def describe(col: dict[str, Any]) -> list[str]:
    """Text lines for the ``mixture`` of one column profile."""
    m = col.get("mixture")
    if not isinstance(m, dict):
        return []
    parts = ", ".join(
        f"{c['weight']:.0%} N({c['mean']:.4g}, {c['sd']:.4g})" for c in m.get("components", [])
    )
    kind = "multimodal" if m.get("multimodal") else "unimodal or minor components"
    return [f"mixture: k={m['k']} ({kind}): {parts}"]
