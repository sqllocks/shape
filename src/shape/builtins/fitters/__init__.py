"""Built-in distribution fitter (``shape.fitters``): the profiler's own fit, as a plugin."""

from __future__ import annotations

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.plugins.api.v1 import FitResult
from shape.profile.fitting import detect_distribution

SHAPE_API = "1.0"


class AutoFitter:
    """The best of normal, uniform, exponential and lognormal by KS statistic, exactly as
    ``shape.profile`` picks it (it calls the same function). Parameters use scipy's ``loc`` and
    ``scale`` (and ``s`` for lognormal); ``ks`` is ``1 - fit_score``."""

    name = "auto"
    families = ("normal", "uniform", "exponential", "lognormal")

    def fit(self, sample: pa.Array) -> FitResult | None:
        values = pc.cast(sample, pa.float64()).drop_null()
        arr = np.asarray(values.to_numpy(zero_copy_only=False), dtype=np.float64)
        arr = arr[np.isfinite(arr)]
        out = detect_distribution(arr)
        if out["distribution"] is None or out["fit_score"] is None:
            return None
        return FitResult(
            str(out["distribution"]),
            {k: float(v) for k, v in out["distribution_params"].items()},
            round(1.0 - float(out["fit_score"]), 4),
        )


__all__ = ["SHAPE_API", "AutoFitter"]
