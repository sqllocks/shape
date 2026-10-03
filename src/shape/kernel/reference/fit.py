"""Pure-numpy twin of the Rust distribution-fitting kernel (``rust/shape-kernel/src/fit.rs``).

It wraps ``shape.profile.reference.numerics`` (the line-by-line numpy port of the scipy routines,
bitwise identical to the baseline on every T-22 dataset) in the same interface as the native
``fit_distribution(sample, full)``.
"""

from __future__ import annotations

from typing import Any

import numpy as np

SAMPLE_SIZE = 2000


def sample_for_fitting(values: np.ndarray) -> np.ndarray:
    """The fitting sample: all values, or 2000 drawn without replacement by
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


def lognorm_probe(data: Any, loc: float) -> tuple[float, float, float, float]:
    """``(shape, scale, dL/dloc, loglik)`` of the lognormal objective at ``loc``, evaluated as
    the reference ``_lognorm_fit`` evaluates them (twin of the native probe)."""
    import pyarrow as pa  # type: ignore[import-untyped]

    from shape.profile.reference.numerics import _lognorm_nnlf

    arr = data if isinstance(data, pa.Array) else pa.array(data)
    if not pa.types.is_float64(arr.type):
        raise ValueError("lognorm_probe needs a float64 array")
    x = np.asarray(arr.to_numpy(zero_copy_only=False), dtype=np.float64)
    with np.errstate(all="ignore"):
        logs = np.log(x - loc)
        scale = np.exp(logs.mean())
        shape = np.sqrt(np.mean(np.square(logs - np.log(scale))))
        shifted = x - loc
        dl = np.sum((1 + np.log(shifted / scale) / shape**2) / shifted)
        ll = -_lognorm_nnlf((shape, loc, scale), x)
    return float(shape), float(scale), float(dl), float(ll)


def numpy_loops_mode() -> str:
    """``"native"``: the twin evaluates ``log``/``exp`` with numpy's own loops."""
    return "native"
