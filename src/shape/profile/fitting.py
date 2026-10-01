"""Distribution detection and fit score, on the native fitting kernel (P1-08).

``detect_distribution`` picks the best of
normal, uniform, exponential and lognormal by KS statistic among those whose exact KS p-value
exceeds 0.05, on a 2000-value sample, then reports a ``fit_score`` from a full refit.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]

from shape.kernel.dispatch import get_kernel
from shape.kernel.reference.fit import sample_for_fitting


def detect_distribution(values: Any) -> dict[str, Any]:
    """``{distribution, distribution_params, fit_score}`` for a column of numbers (NaN dropped
    by the caller; ``None``s everywhere when there are fewer than 20 values or nothing fits)."""
    full = np.ascontiguousarray(np.asarray(values, dtype=np.float64))
    if len(full) < 20:
        return {"distribution": None, "distribution_params": None, "fit_score": None}
    sample = sample_for_fitting(full)
    out = get_kernel().fit_distribution(pa.array(sample), pa.array(full))
    return dict(out)
