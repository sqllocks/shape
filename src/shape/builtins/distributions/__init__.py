"""Built-in distributions (``shape.distributions``), in the parameters the fitter reports.

All use scipy's convention: ``loc`` and ``scale`` (and ``s`` for lognormal). Every draw comes
from the chunk's keyed stream, so a context always yields the same array.
"""

from __future__ import annotations

from collections.abc import Mapping

import pyarrow as pa  # type: ignore[import-untyped]

from shape.builtins._rng import chunk_generator
from shape.plugins.api.v1 import GenerationContext

SHAPE_API = "1.0"


def _param(params: Mapping[str, float], key: str, default: float | None = None) -> float:
    if key in params:
        return float(params[key])
    if default is None:
        raise ValueError(f"missing parameter {key!r}")
    return default


def _scale(params: Mapping[str, float]) -> float:
    scale = _param(params, "scale", 1.0)
    if not scale > 0:
        raise ValueError("scale must be positive")
    return scale


class Normal:
    """``loc + scale * N(0, 1)``."""

    name = "normal"

    def sample(self, params: Mapping[str, float], ctx: GenerationContext) -> pa.Array:
        rng = chunk_generator(ctx)
        return pa.array(
            _param(params, "loc", 0.0) + _scale(params) * rng.standard_normal(ctx.n_rows)
        )


class Uniform:
    """Uniform on ``[loc, loc + scale)``."""

    name = "uniform"

    def sample(self, params: Mapping[str, float], ctx: GenerationContext) -> pa.Array:
        rng = chunk_generator(ctx)
        return pa.array(_param(params, "loc", 0.0) + _scale(params) * rng.random(ctx.n_rows))


class Exponential:
    """``loc + scale * Exp(1)``."""

    name = "exponential"

    def sample(self, params: Mapping[str, float], ctx: GenerationContext) -> pa.Array:
        rng = chunk_generator(ctx)
        return pa.array(
            _param(params, "loc", 0.0) + _scale(params) * rng.standard_exponential(ctx.n_rows)
        )


class Lognormal:
    """``loc + scale * exp(s * N(0, 1))``."""

    name = "lognormal"

    def sample(self, params: Mapping[str, float], ctx: GenerationContext) -> pa.Array:
        import numpy as np

        s = _param(params, "s")
        if not s > 0:
            raise ValueError("s must be positive")
        rng = chunk_generator(ctx)
        draws = np.exp(s * rng.standard_normal(ctx.n_rows))
        return pa.array(_param(params, "loc", 0.0) + _scale(params) * draws)


__all__ = ["SHAPE_API", "Exponential", "Lognormal", "Normal", "Uniform"]
