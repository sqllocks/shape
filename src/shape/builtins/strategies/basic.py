"""Built-in strategies ``uuid`` and ``weighted_enum`` (``sequence`` is in the package root).

Both are row addressed: the value of row ``r`` depends only on the run seed, the table, the
column and ``r`` (``docs/GENERATION_STRATEGIES.md``).
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]

from shape.generation import kernel_ops
from shape.generation.arrowkit import array as arrow_array
from shape.generation.strategy_kit import StrategyError, require, stream, where
from shape.plugins.api.v1 import GenerationContext

SHAPE_API = "1.0"


class Uuid:
    """Version-4 UUIDs as lowercase ``8-4-4-4-12`` strings, from the column's stream (two words
    per row). Unique with overwhelming probability, and reproducible for a seed."""

    name = "uuid"

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        return kernel_ops.uuid4(stream(ctx, "v"), ctx.row_start, ctx.n_rows)


@lru_cache(maxsize=64)
def _labels(keys: tuple[str, ...]) -> tuple[np.ndarray[Any, Any] | None, pa.Array]:
    """(float values, or None) and the string pool of a label list. Numeric labels give a float
    column: a formula can use it as a number."""
    try:
        numbers: np.ndarray[Any, Any] | None = np.array([float(k) for k in keys], dtype=np.float64)
    except (TypeError, ValueError):
        numbers = None
    return numbers, arrow_array([str(k) for k in keys], type=pa.string())


class WeightedEnum:
    """A value drawn from ``spec['values']``, a mapping from value to weight.

    Weights are relative (they are normalised) and may be zero. When every key reads as a number
    the column is ``float64``; otherwise it is a string. The draw uses an alias table (two words
    per row), so it costs the same for 3 values or 30,000.
    """

    name = "weighted_enum"

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        values = spec.get("values")
        if not isinstance(values, Mapping) or not values:
            raise StrategyError(
                f"weighted_enum strategy requires a non-empty 'values' mapping for {where(ctx)}"
            )
        weights = [float(w) for w in values.values()]
        if any(w < 0 or w != w for w in weights) or sum(weights) <= 0:
            raise StrategyError(
                f"weighted_enum weights must be non-negative with a positive sum ({where(ctx)})"
            )
        numbers, pool = _labels(tuple(str(k) for k in values))
        index = kernel_ops.alias_draw(
            kernel_ops.alias_table(weights), stream(ctx, "v"), ctx.row_start, ctx.n_rows
        )
        if numbers is not None:
            return arrow_array(numbers[index])
        return kernel_ops.pool_take(pool, index)


__all__ = ["SHAPE_API", "Uuid", "WeightedEnum", "require"]
