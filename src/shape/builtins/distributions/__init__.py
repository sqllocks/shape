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

from shape.generation.arrowkit import array as arrow_array
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
    generator_version = 1

    def sample(self, params: Mapping[str, float], ctx: GenerationContext) -> pa.Array:
        z = stream(ctx, "dist").normal(ctx.row_start, ctx.n_rows)
        return arrow_array(_param(params, "loc", 0.0) + _scale(params) * z)


class Uniform:
    """Uniform on ``[loc, loc + scale)``."""

    name = "uniform"
    generator_version = 1

    def sample(self, params: Mapping[str, float], ctx: GenerationContext) -> pa.Array:
        u = stream(ctx, "dist").uniform(ctx.row_start, ctx.n_rows)
        return arrow_array(_param(params, "loc", 0.0) + _scale(params) * u)


class Exponential:
    """``loc + scale * Exp(1)``."""

    name = "exponential"
    generator_version = 1

    def sample(self, params: Mapping[str, float], ctx: GenerationContext) -> pa.Array:
        u = stream(ctx, "dist").uniform(ctx.row_start, ctx.n_rows)
        return arrow_array(_param(params, "loc", 0.0) + _scale(params) * -np.log1p(-u))


class Lognormal:
    """``loc + scale * exp(s * N(0, 1))``."""

    name = "lognormal"
    generator_version = 1

    def sample(self, params: Mapping[str, float], ctx: GenerationContext) -> pa.Array:
        s = _param(params, "s")
        if not s > 0:
            raise ValueError("s must be positive")
        z = stream(ctx, "dist").normal(ctx.row_start, ctx.n_rows)
        return arrow_array(_param(params, "loc", 0.0) + _scale(params) * np.exp(s * z))


class FamilyDistribution:
    """A ``shape.distributions`` entry for one family; ``params`` are the family's own."""

    name = ""
    family: Family

    @property
    def generator_version(self) -> int:
        """The version of the family this entry wraps."""
        return int(self.family.generator_version)

    @property
    def generator_version_range(self) -> tuple[int, int]:
        latest = self.generator_version
        return (1, latest) if hasattr(self.family, "sample_versioned") else (latest, latest)

    def sample(self, params: Mapping[str, float], ctx: GenerationContext) -> pa.Array:
        values = self.family.sample(stream(ctx, "dist"), ctx.row_start, ctx.n_rows, params)
        return arrow_array(values)

    def sample_versioned(
        self, params: Mapping[str, float], ctx: GenerationContext, version: int
    ) -> pa.Array:
        select = getattr(self.family, "sample_versioned", None)
        if select is None:
            return self.sample(params, ctx)
        values = select(stream(ctx, "dist"), ctx.row_start, ctx.n_rows, params, version)
        return arrow_array(values)


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


class Gamma(FamilyDistribution):
    """Gamma with shape ``k`` and scale ``theta``."""

    name = "gamma"
    family = FAMILIES["gamma"]


class Beta(FamilyDistribution):
    """Beta with shape parameters ``a`` and ``b``."""

    name = "beta"
    family = FAMILIES["beta"]


class Weibull(FamilyDistribution):
    """Weibull with shape ``k`` and scale ``lam``."""

    name = "weibull"
    family = FAMILIES["weibull"]


class Triangular(FamilyDistribution):
    """Triangular on ``[low, high]`` with its peak at ``mode``."""

    name = "triangular"
    family = FAMILIES["triangular"]


class NegativeBinomial(FamilyDistribution):
    """Failures before the ``r``-th success, success probability ``p``."""

    name = "negative_binomial"
    family = FAMILIES["negative_binomial"]


class PowerLawCutoff(FamilyDistribution):
    """Density proportional to ``x ** -alpha * exp(-lam * x)`` for ``x >= xmin``."""

    name = "power_law_cutoff"
    family = FAMILIES["power_law_cutoff"]


class Mixture(FamilyDistribution):
    """A weighted mixture of other families (``components``)."""

    name = "mixture"
    family = FAMILIES["mixture"]


class Truncated(FamilyDistribution):
    """A family restricted to ``[low, high]`` (``base``, ``base_params``, ``low``, ``high``)."""

    name = "truncated"
    family = FAMILIES["truncated"]


class Histogram(FamilyDistribution):
    """An empirical histogram (``edges`` and ``weights``)."""

    name = "histogram"
    family = FAMILIES["histogram"]


__all__ = [
    "SHAPE_API",
    "Bernoulli",
    "Beta",
    "Exponential",
    "FamilyDistribution",
    "Gamma",
    "Geometric",
    "Histogram",
    "Lognormal",
    "LogNormalFamily",
    "Mixture",
    "NegativeBinomial",
    "Normal",
    "Pareto",
    "Poisson",
    "PowerLawCutoff",
    "Triangular",
    "Truncated",
    "Uniform",
    "Weibull",
    "Zipf",
]
