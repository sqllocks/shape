"""Vectorized Gaussian-copula-style joint numeric reconstruction."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class JointModel:
    fields: tuple[str, ...]
    means: tuple[float, ...]
    stddevs: tuple[float, ...]
    correlation: tuple[tuple[float, ...], ...]


def fit_joint_numeric(data, fields=None):
    import numpy as np

    fields = tuple(fields or data.keys())
    x = np.column_stack([np.asarray(data[f], dtype=float) for f in fields])
    means = np.nanmean(x, axis=0)
    std = np.nanstd(x, axis=0)
    std = np.where(std <= 0, 1, std)
    corr = np.corrcoef(np.nan_to_num((x - means) / std).T)
    if corr.ndim == 0:
        corr = np.ones((1, 1))
    corr = np.nan_to_num(corr, nan=0)
    np.fill_diagonal(corr, 1)
    return JointModel(
        fields, tuple(means), tuple(std), tuple(tuple(float(v) for v in r) for r in corr)
    )


def generate_joint_numeric(model, n, seed=0):
    import numpy as np

    C = np.asarray(model.correlation, dtype=float)
    C = (C + C.T) / 2
    w, v = np.linalg.eigh(C)
    C = (v * np.maximum(w, 1e-8)) @ v.T
    d = np.sqrt(np.diag(C))
    C = C / np.outer(d, d)
    rng = np.random.default_rng(seed)
    # Explicit factorization + standard normals avoids multivariate_normal's repeated covariance
    # checks/decomposition.
    L = np.linalg.cholesky(C + np.eye(len(model.fields)) * 1e-12)
    z = rng.standard_normal((n, len(model.fields))) @ L.T
    x = z * np.asarray(model.stddevs) + np.asarray(model.means)
    return {f: x[:, i] for i, f in enumerate(model.fields)}
