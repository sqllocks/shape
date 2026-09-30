"""Pure-numpy twin of the Rust distribution-fitting kernel (``rust/shape-kernel/src/fit.rs``).

It wraps ``shape.profile.reference.numerics`` (the line-by-line numpy port of the scipy routines,
bitwise identical to Spindle on every T-22 dataset) in the same interface as the native
``fit_distribution(sample, full)``.
"""

from __future__ import annotations

from typing import Any

import numpy as np

SAMPLE_SIZE = 2000


def sample_for_fitting(values: np.ndarray) -> np.ndarray:
    """Spindle's fitting sample: all values, or 2000 drawn without replacement by
    ``default_rng(42)`` when there are more."""
    if len(values) > SAMPLE_SIZE:
        return np.asarray(np.random.default_rng(42).choice(values, size=SAMPLE_SIZE, replace=False))
    return values


def fit_distribution(sample: Any, full: Any | None = None) -> dict[str, Any]:
    from shape.profile.reference.numerics import (
        _FitError,
        _ks_stat_sorted,
        detect_distribution,
        fit,
    )

    sample_a = np.asarray(sample, dtype=np.float64)
    full_a = sample_a if full is None else np.asarray(full, dtype=np.float64)
    name, params = detect_distribution(sample_a)
    score = None
    if name is not None and len(full_a) >= 20:
        try:
            with np.errstate(all="ignore"):
                p = fit(name, full_a)
                d = _ks_stat_sorted(np.sort(full_a), name, p)
            score = round(1.0 - d, 4)
        except (_FitError, ValueError, FloatingPointError, ZeroDivisionError):
            score = None
    return {"distribution": name, "distribution_params": params, "fit_score": score}
