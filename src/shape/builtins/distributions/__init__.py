"""Built-in distributions (``shape.distributions``).

``normal``, ``uniform``, ``exponential`` and ``lognormal`` take the parameters the fitter reports
(scipy's ``loc`` and ``scale``, and ``s`` for lognormal). The other entries wrap the families of
:mod:`shape.builtins.distributions.families` and take the family's own parameters (``mu``,
``sigma``, ``alpha``, ...). Every draw comes from the column's Philox stream and is addressed by
row, so a context yields the same values however the table is chunked (T-16).
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]

from shape.generation.strategy_kit import stream
from shape.plugins.api.v1 import GenerationContext

from .families import FAMILIES, Family

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
        z = stream(ctx, "dist").normal(ctx.row_start, ctx.n_rows)
        return pa.array(_param(params, "loc", 0.0) + _scale(params) * z)


class Uniform:
    """Uniform on ``[loc, loc + scale)``."""

    name = "uniform"

    def sample(self, params: Mapping[str, float], ctx: GenerationContext) -> pa.Array:
        u = stream(ctx, "dist").uniform(ctx.row_start, ctx.n_rows)
        return pa.array(_param(params, "loc", 0.0) + _scale(params) * u)


class Exponential:
    """``loc + scale * Exp(1)``."""

    name = "exponential"

    def sample(self, params: Mapping[str, float], ctx: GenerationContext) -> pa.Array:
        u = stream(ctx, "dist").uniform(ctx.row_start, ctx.n_rows)
        return pa.array(_param(params, "loc", 0.0) + _scale(params) * -np.log1p(-u))


class Lognormal:
    """``loc + scale * exp(s * N(0, 1))``."""

    name = "lognormal"

    def sample(self, params: Mapping[str, float], ctx: GenerationContext) -> pa.Array:
        s = _param(params, "s")
        if not s > 0:
            raise ValueError("s must be positive")
        z = stream(ctx, "dist").normal(ctx.row_start, ctx.n_rows)
        return pa.array(_param(params, "loc", 0.0) + _scale(params) * np.exp(s * z))


class FamilyDistribution:
    """A ``shape.distributions`` entry for one family; ``params`` are the family's own."""

    name = ""
    family: Family

    def sample(self, params: Mapping[str, float], ctx: GenerationContext) -> pa.Array:
        values = self.family.sample(stream(ctx, "dist"), ctx.row_start, ctx.n_rows, params)
        return pa.array(values)


class LogNormalFamily(FamilyDistribution):
    """``exp(mu + sigma * N(0, 1))``."""

    name = "log_normal"
    family = FAMILIES["log_normal"]


class Pareto(FamilyDistribution):
    """Type I Pareto with shape ``alpha`` and minimum ``xm``."""

    name = "pareto"
    family = FAMILIES["pareto"]


class Zipf(FamilyDistribution):
    """Zipf on ``1 .. max`` with exponent ``a``."""

    name = "zipf"
    family = FAMILIES["zipf"]


class Geometric(FamilyDistribution):
    """Trials to the first success, success probability ``p``."""

    name = "geometric"
    family = FAMILIES["geometric"]


class Poisson(FamilyDistribution):
    """Poisson with mean ``lam``."""

    name = "poisson"
    family = FAMILIES["poisson"]


class Bernoulli(FamilyDistribution):
    """0 or 1, with ``P(1) = p``."""

    name = "bernoulli"
    family = FAMILIES["bernoulli"]


__all__ = [
    "SHAPE_API",
    "Bernoulli",
    "Exponential",
    "FamilyDistribution",
    "Geometric",
    "Lognormal",
    "LogNormalFamily",
    "Normal",
    "Pareto",
    "Poisson",
    "Uniform",
    "Zipf",
]
