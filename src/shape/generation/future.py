"""Correlated columns and missing values: the two pieces ``shape generate --from`` rebuilds from a
profile (stable interface: :func:`gaussian_copula`, :func:`missingness`).

:func:`gaussian_copula` draws normal scores with a target correlation and, given each column's
marginal distribution, maps them to values of that marginal (``Phi(z)`` through the marginal's
quantile function), so the columns keep their own distributions and have the target dependence.
:func:`missingness` returns the values with the missing ones nulled, and the mask.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]

Floats = npt.NDArray[np.float64]


class Marginal(Protocol):
    """A quantile function: ``ppf(u)`` maps uniforms on (0, 1) to values (a scipy frozen
    distribution has one)."""

    def ppf(self, q: Floats) -> Floats: ...


def normal_cdf(z: Floats) -> Floats:
    """``Phi(z)``: scipy's ``ndtr`` when scipy is installed, else ``math.erf`` per value."""
    try:
        from scipy.special import ndtr  # type: ignore[import-untyped,unused-ignore]
    except ImportError:
        erf = np.array([math.erf(v / math.sqrt(2.0)) for v in z.ravel()]).reshape(z.shape)
        out: Floats = 0.5 * (1.0 + erf)
        return out
    return np.asarray(ndtr(z), dtype=np.float64)


def correlation_matrix(corr: Any) -> Floats:
    """The nearest usable correlation matrix: symmetric, eigenvalues at least 1e-9, rescaled to a
    unit diagonal (clipping eigenvalues alone leaves variances different from 1)."""
    c = np.asarray(corr, dtype=np.float64)
    if c.ndim != 2 or c.shape[0] != c.shape[1]:
        raise ValueError("the correlation matrix must be square")
    c = (c + c.T) / 2.0
    w, v = np.linalg.eigh(c)
    c = (v * np.maximum(w, 1e-9)) @ v.T
    d = np.sqrt(np.diag(c))
    out: Floats = c / np.outer(d, d)
    return out


def gaussian_copula(
    corr: Any,
    n: int,
    seed: int = 0,
    marginals: Sequence[Marginal | Callable[[Floats], Floats] | None] | None = None,
) -> Floats:
    """``n`` rows of ``k`` correlated columns.

    Without ``marginals`` the columns are standard normal scores with correlation ``corr``. With
    them, column ``j`` is ``marginals[j]`` applied to ``Phi(score)``: a quantile function (an
    object with ``ppf``, or a callable on uniforms; ``None`` keeps the normal scores of that
    column), so every column has its own marginal distribution and the Gaussian dependence of
    ``corr``."""
    c = correlation_matrix(corr)
    k = len(c)
    if marginals is not None and len(marginals) != k:
        raise ValueError(f"{k} columns need {k} marginals, got {len(marginals)}")
    z: Floats = np.random.default_rng(seed).multivariate_normal(
        np.zeros(k), c, n, method="cholesky"
    )
    if marginals is None:
        return z
    out = z.copy()
    u = np.clip(normal_cdf(z), 1e-12, 1.0 - 1e-12)
    for j, marginal in enumerate(marginals):
        if marginal is None:
            continue
        ppf = marginal.ppf if hasattr(marginal, "ppf") else marginal
        out[:, j] = np.asarray(ppf(u[:, j]), dtype=np.float64)
    return out


def missingness(values: Any, rate: float, seed: int = 0) -> tuple[pa.Array, npt.NDArray[np.bool_]]:
    """``(values with each entry missing with probability rate, the mask of missing entries)``.

    The result is an Arrow array of the input's type with real nulls where the mask is true."""
    if not 0.0 <= rate <= 1.0:
        raise ValueError("the missing rate must be between 0 and 1")
    arr = values if isinstance(values, (pa.Array, pa.ChunkedArray)) else pa.array(values)
    if isinstance(arr, pa.ChunkedArray):
        arr = arr.combine_chunks()
    mask: npt.NDArray[np.bool_] = np.random.default_rng(seed).random(len(arr)) < rate
    import pyarrow.compute as pc  # type: ignore[import-untyped]

    return pc.if_else(pa.array(mask), pa.scalar(None, type=arr.type), arr), mask


__all__ = ["Marginal", "correlation_matrix", "gaussian_copula", "missingness", "normal_cdf"]
