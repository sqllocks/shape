"""What every generation strategy builds on (stable interface).

A strategy is any object with a ``name`` and ``generate(spec, ctx)`` (plugin group
``shape.strategies``; see ``docs/GENERATION_STRATEGIES.md``). It must be **row addressed**: the
value of row ``r`` is a function of the run seed, the table, the column and ``r`` (plus the
spec and the other columns of the same row), never of the chunk. These helpers make that the
easy path:

* :func:`stream` is the column's Philox stream; ``stream(ctx, "label")`` gives a separate stream
  for each independent random choice a strategy makes.
* :func:`require` and :func:`spec_params` read the generator spec with clear errors.
* :func:`column_scale` and :func:`round_to_scale` apply the column's decimal scale.

Stable interface: ``stream``, ``require``, ``spec_params``, ``column_scale``, ``round_to_scale``
and ``StrategyError``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import numpy.typing as npt

from shape.errors import ShapeError
from shape.plugins.api.v1 import GenerationContext

from .rng import RowStream


class StrategyError(ShapeError, ValueError):
    """A generator spec a strategy cannot use. The message names the table and column."""


def where(ctx: GenerationContext) -> str:
    """``table.column`` of the context, for messages."""
    return f"{ctx.table}.{ctx.column}"


def stream(ctx: GenerationContext, label: str = "v") -> RowStream:
    """The Philox stream of this column and ``label``, keyed ``(seed, table, column, label)``."""
    return RowStream(ctx.seed, ctx.table, ctx.column, label)


def require(spec: Mapping[str, Any], key: str, ctx: GenerationContext, strategy: str) -> Any:
    """``spec[key]``, or a :class:`StrategyError` naming the strategy and the column."""
    if key not in spec or spec[key] is None:
        raise StrategyError(f"{strategy} strategy requires {key!r} for column {where(ctx)}")
    return spec[key]


def spec_params(spec: Mapping[str, Any]) -> Mapping[str, Any]:
    """The parameters of a ``distribution``-style spec: its ``params`` mapping when there is a
    non-empty one, else the spec itself (both forms are accepted)."""
    nested = spec.get("params")
    return nested if isinstance(nested, Mapping) and nested else spec


def column_scale(ctx: GenerationContext) -> int | None:
    """The decimal ``scale`` of the column being built, if its definition has one."""
    column = getattr(ctx, "column_def", None)
    scale = getattr(column, "scale", None)
    return None if scale is None else int(scale)


def round_to_scale(
    values: npt.NDArray[np.float64], ctx: GenerationContext
) -> npt.NDArray[np.float64]:
    """``values`` rounded to the column's decimal scale (unchanged without one)."""
    scale = column_scale(ctx)
    return values if scale is None else np.round(values, scale)
