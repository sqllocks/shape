"""What the relational strategies share (P4-04d): the engine behind a context, whole columns of a
table, parent key pools, and the index draws of a foreign key.

Everything here is a function of the schema, the seed and the spec, so a chunk read in any order
finds the same values. Whole-table results are kept with ``Engine.cached``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.generation.arrowkit import array as arrow_array
from shape.generation.arrowkit import scalar as arrow_scalar
from shape.generation.engine import ArrayKeys, Engine, KeyPool, RangeKeys
from shape.generation.strategy_kit import StrategyError, where
from shape.plugins.api.v1 import GenerationContext

BLOCK_ROWS = 1 << 20  # rows per call when a whole column is built
ZIPF_HEAD = 1 << 20  # ranks with an exact table; the tail of a larger pool is integrated

Ints = npt.NDArray[np.int64]
Floats = npt.NDArray[np.float64]


def engine_of(ctx: GenerationContext, strategy: str) -> Engine:
    """The engine of an engine context; a bare ``GenerationContext`` has none."""
    engine = getattr(ctx, "engine", None)
    if engine is None:
        raise StrategyError(f"{strategy} strategy needs the generation engine ({where(ctx)})")
    return engine  # type: ignore[no-any-return]


def whole_column(
    ctx: GenerationContext, table: str, column: str, strategy: str, *, keep: bool = True
) -> pa.Array:
    """Every row of ``table.column`` (nulls applied), built in blocks. Kept for the engine's
    lifetime unless ``keep`` is false."""
    engine = engine_of(ctx, strategy)
    tdef = engine.schema.tables.get(table)
    if tdef is None or column not in tdef.columns:
        raise StrategyError(
            f"{strategy} strategy for {where(ctx)}: table '{table}' has no column '{column}'"
        )

    def build() -> pa.Array:
        total = engine.row_counts.get(table, 0)
        parts = [
            engine.generate_column(table, column, start, min(BLOCK_ROWS, total - start))
            for start in range(0, total, BLOCK_ROWS)
        ] or [engine.generate_column(table, column, 0, 0)]
        return parts[0] if len(parts) == 1 else pa.concat_arrays(parts)

    return build() if not keep else engine.cached(("column", table, column), build)


def parent_pool(ctx: GenerationContext, table: str, column: str, strategy: str) -> KeyPool:
    """The values of ``table.column`` as a key pool. The single-column primary key of another
    table (or of this one, when it is a plain sequence) comes from the engine's pools; any other
    column is generated in full."""
    engine = engine_of(ctx, strategy)
    tdef = engine.schema.tables.get(table)
    if tdef is None:
        raise StrategyError(f"{strategy} strategy for {where(ctx)}: there is no table '{table}'")
    if column not in tdef.columns:
        raise StrategyError(
            f"{strategy} strategy for {where(ctx)}: table '{table}' has no column '{column}'"
        )
    if tdef.primary_key == [column]:
        pk = tdef.columns[column]
        if table != ctx.table or (pk.strategy == "sequence" and not (pk.nullable and pk.null_rate)):
            return engine.key_pool(table)
    return ArrayKeys(whole_column(ctx, table, column, strategy))


def take_keys(pool: KeyPool, indices: Ints, null: npt.NDArray[np.bool_] | None = None) -> pa.Array:
    """``pool`` at ``indices`` (row numbers); where ``null`` is set the result is null."""
    if null is None or not null.any():
        return pool.take(indices)
    safe = np.where(null, 0, indices)
    if isinstance(pool, RangeKeys):
        return arrow_array(pool.start + safe * pool.step, mask=null)
    if isinstance(pool, ArrayKeys):
        return pc.take(pool.values, arrow_array(safe, mask=null))
    taken = pool.take(safe)
    return pc.if_else(arrow_array(null), arrow_scalar(None, type=taken.type), taken)


@lru_cache(maxsize=4)
def _zipf_head(alpha: float, head: int) -> Floats:
    cum: Floats = np.cumsum(np.arange(1, head + 1, dtype=np.float64) ** (-alpha))
    cum.flags.writeable = False
    return cum


def zipf_index(u: Floats, pool: int, alpha: float) -> Ints:
    """Row numbers ``0 .. pool - 1`` with ``P(k)`` proportional to ``(k + 1) ** -alpha``: Zipf
    truncated to the pool, from uniforms ``u``. Ranks up to ``ZIPF_HEAD`` are exact; the ranks of
    a larger pool beyond it are drawn by inverting the integral of the same power law."""
    head = min(pool, ZIPF_HEAD)
    cum = _zipf_head(alpha, head)
    if pool <= ZIPF_HEAD:
        k = np.searchsorted(cum / cum[-1], u, side="right")
        return np.minimum(k, pool - 1).astype(np.int64)
    low, high = ZIPF_HEAD + 0.5, pool + 0.5
    if alpha == 1.0:
        tail = float(np.log(high / low))
    else:
        tail = float((low ** (1 - alpha) - high ** (1 - alpha)) / (alpha - 1))
    target = u * (cum[-1] + tail)
    in_head = target < cum[-1]
    out = np.minimum(np.searchsorted(cum, target, side="right"), head - 1)
    rest = np.maximum(target - cum[-1], 0.0)
    if alpha == 1.0:
        x = low * np.exp(rest)
    else:
        x = (low ** (1 - alpha) - rest * (alpha - 1)) ** (1.0 / (1 - alpha))
    rank = np.clip(np.floor(x + 0.5).astype(np.int64) - 1, head, pool - 1)
    result: Ints = np.where(in_head, out, rank).astype(np.int64)
    return result


def pareto_index(u: Floats, pool: int, alpha: float) -> Ints:
    """Row numbers from a heavy-tailed (Lomax) draw cut at its 99.5th percentile and scaled to the
    pool, the shape of a few very popular parents and a long thin tail."""
    raw = (1.0 - u) ** (-1.0 / alpha) - 1.0
    cap = 0.005 ** (-1.0 / alpha) - 1.0
    scaled = np.minimum(raw, cap) / (cap + 1e-9) * pool
    result: Ints = np.clip(scaled.astype(np.int64), 0, pool - 1)
    return result


def positive(spec: dict[str, Any] | Any, key: str, default: float, ctx: GenerationContext) -> float:
    """``spec[key]`` as a positive number (``default`` when absent)."""
    raw = spec.get(key, default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise StrategyError(f"{key} must be a number for {where(ctx)}, got {raw!r}") from None
    if not value > 0:
        raise StrategyError(f"{key} must be positive for {where(ctx)}, got {raw!r}")
    return value
