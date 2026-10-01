"""``lifecycle`` (P4-04d): a phase label drawn from weighted phases."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.generation import kernel_ops
from shape.generation.strategy_kit import StrategyError, stream, where
from shape.plugins.api.v1 import GenerationContext

SHAPE_API = "1.0"


class Lifecycle:
    """A phase label drawn with probability proportional to its weight.

    ``spec['phases']`` (or ``spec['values']``) maps each phase to a weight (relative; zero is
    allowed). The labels are strings, whatever they look like; the draw uses an alias table, two
    words per row.
    """

    name = "lifecycle"

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        phases = spec.get("phases") or spec.get("values")
        if not isinstance(phases, Mapping) or not phases:
            raise StrategyError(
                f"lifecycle strategy requires a non-empty 'phases' mapping for {where(ctx)}"
            )
        weights = [float(w) for w in phases.values()]
        if any(w < 0 or w != w for w in weights) or sum(weights) <= 0:
            raise StrategyError(
                f"lifecycle weights must be non-negative with a positive sum ({where(ctx)})"
            )
        index = kernel_ops.alias_draw(
            kernel_ops.alias_table(weights), stream(ctx, "v"), ctx.row_start, ctx.n_rows
        )
        return kernel_ops.pool_take(pa.array([str(k) for k in phases], type=pa.string()), index)


__all__ = ["SHAPE_API", "Lifecycle"]
