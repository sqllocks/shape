"""Built-in strategies ``distribution`` and ``empirical``: numeric columns from a statistical
family or from a stored quantile fingerprint. Row addressed (``docs/GENERATION_STRATEGIES.md``)."""

from __future__ import annotations

import warnings
from collections.abc import Mapping
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]

from shape.builtins.distributions.families import FAMILIES, FamilyError
from shape.generation.strategy_kit import (
    StrategyError,
    require,
    round_to_scale,
    spec_params,
    stream,
    where,
)
from shape.plugins.api.v1 import GenerationContext

SHAPE_API = "1.0"


def _plugin_distribution(name: str) -> Any:
    from shape.plugins.host import default_host

    return default_host().try_get("shape.distributions", name)


class Distribution:
    """Values from a distribution family.

    ``spec['distribution']`` names the family (default ``uniform``); its parameters are in
    ``spec['params']`` or directly in the spec. Built-in families: ``uniform`` (``min``,
    ``max``), ``normal`` (``mean``, ``std_dev``), ``log_normal`` (``mean``, ``sigma``),
    ``pareto`` (``alpha``, ``min``), ``zipf`` (``alpha``, ``max``), ``geometric`` (``p``),
    ``poisson`` (``lambda``) and ``bernoulli`` (``probability``); the families of
    :mod:`shape.builtins.distributions.families` also accept their own parameter names, and any
    ``shape.distributions`` plugin can be named. ``min`` and ``max`` then clip every value, and
    the column's decimal ``scale`` rounds it. The column is ``float64``.
    """

    name = "distribution"

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        dist = str(spec.get("distribution", "uniform"))
        params = spec_params(spec)
        family = FAMILIES.get(dist)
        if family is not None:
            try:
                values = family.sample(
                    stream(ctx, "v"), ctx.row_start, ctx.n_rows, family.from_spec(params)
                )
            except FamilyError as exc:
                raise StrategyError(f"{exc} ({where(ctx)})") from exc
        else:
            plugin = _plugin_distribution(dist)
            if plugin is None:
                known = ", ".join(sorted(FAMILIES))
                raise StrategyError(
                    f"unknown distribution {dist!r} for {where(ctx)}; known: {known}"
                )
            values = np.asarray(plugin.sample(dict(params), ctx).to_numpy(zero_copy_only=False))
            values = values.astype(np.float64, copy=False)
        if params.get("min") is not None:
            values = np.maximum(values, float(params["min"]))
        if params.get("max") is not None:
            values = np.minimum(values, float(params["max"]))
        return pa.array(round_to_scale(values, ctx))


_PERCENTILE_KEYS = ("p1", "p5", "p10", "p25", "p50", "p75", "p90", "p95", "p99")
_PERCENTILE_VALUES = (0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99)


class Empirical:
    """Values drawn by inverse-transform sampling of a quantile fingerprint.

    ``spec['quantiles']`` maps ``p1, p5, p10, p25, p50, p75, p90, p95, p99`` to values (and may
    add the tail anchors ``p0_5`` and ``p99_5``); ``interpolation`` is ``linear`` (default) or
    ``cubic`` (needs scipy, else linear with a warning); ``min`` and ``max`` clip. A uniform draw
    is mapped through the interpolated quantile function; it does not extrapolate beyond the
    outermost anchors. The column is ``float64``.
    """

    name = "empirical"

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        quantiles = require(spec, "quantiles", ctx, "empirical")
        missing = [k for k in _PERCENTILE_KEYS if k not in quantiles]
        if missing:
            raise StrategyError(
                f"empirical strategy for {where(ctx)} is missing quantile keys: {missing}"
            )
        probs = list(_PERCENTILE_VALUES)
        values = [float(quantiles[k]) for k in _PERCENTILE_KEYS]
        low = quantiles.get("p0_5")
        if low is not None and float(low) <= values[0]:
            probs.insert(0, 0.005)
            values.insert(0, float(low))
        high = quantiles.get("p99_5")
        if high is not None and float(high) >= values[-1]:
            probs.append(0.995)
            values.append(float(high))
        if any(b < a for a, b in zip(values, values[1:], strict=False)):
            raise StrategyError(f"empirical quantiles for {where(ctx)} must not decrease")
        u = stream(ctx, "v").uniform(ctx.row_start, ctx.n_rows)
        p_arr, q_arr = np.asarray(probs), np.asarray(values)
        out = self._interpolate(spec, ctx, u, p_arr, q_arr)
        if spec.get("min") is not None:
            out = np.maximum(out, float(spec["min"]))
        if spec.get("max") is not None:
            out = np.minimum(out, float(spec["max"]))
        return pa.array(out)

    @staticmethod
    def _interpolate(
        spec: Mapping[str, Any],
        ctx: GenerationContext,
        u: np.ndarray[Any, Any],
        probs: np.ndarray[Any, Any],
        values: np.ndarray[Any, Any],
    ) -> np.ndarray[Any, Any]:
        if spec.get("interpolation", "linear") == "cubic":
            try:
                from scipy.interpolate import interp1d  # type: ignore[import-untyped,unused-ignore]
            except ImportError:
                warnings.warn(
                    f"scipy not available; falling back to linear interpolation for {where(ctx)}",
                    ImportWarning,
                    stacklevel=3,
                )
            else:
                fn = interp1d(
                    probs, values, kind="cubic", bounds_error=False,
                    fill_value=(values[0], values[-1]),
                )  # fmt: skip
                return np.asarray(fn(u), dtype=np.float64)
        return np.asarray(np.interp(u, probs, values), dtype=np.float64)


__all__ = ["SHAPE_API", "Distribution", "Empirical"]
