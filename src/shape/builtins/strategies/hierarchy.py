"""Hierarchies inside one table (P4-04d): ``self_referencing`` and ``self_ref_field``.

The level of every row is a function of its row number and the table's row count, and its parent
is drawn from the row's own words, so both are row addressed: a chunk read in any order gives the
same hierarchy (``docs/GENERATION_STRATEGIES.md``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]

from shape.generation.strategy_kit import StrategyError, stream, where
from shape.plugins.api.v1 import GenerationContext

from ._relational import Ints, engine_of, parent_pool, take_keys

SHAPE_API = "1.0"

SR_PREFIX = "_sr_"


def sr_key(table: str, field: str) -> str:
    """The internal name under which ``self_referencing`` hands a field to ``self_ref_field``."""
    return f"{SR_PREFIX}{table}_{field}"


def level_bounds(n: int, levels: int, root_count: int) -> tuple[list[int], list[int]]:
    """Start and end row of every level (1-based levels are indices 0, 1, ...): the first
    ``root_count`` rows (at most ``n // levels``, at least 1) are level 1 and the rest share levels
    2 .. ``levels`` evenly, the first levels taking the remainder."""
    root_count = max(1, min(root_count, n // levels))
    remaining = n - root_count
    per_level = remaining // (levels - 1) if levels > 1 else 0
    extra = remaining - per_level * (levels - 1) if levels > 1 else remaining
    starts, cursor = [0], root_count
    for level in range(2, levels + 1):
        starts.append(cursor)
        cursor += per_level + (1 if level - 2 < extra else 0)
    return starts, starts[1:] + [n]


class SelfReferencing:
    """The parent key of a row of the same table, forming a ``levels``-deep hierarchy.

    ``pk_column`` (earlier in the table) holds the keys; ``levels`` (alias ``max_depth``, 3) is the
    depth and ``root_count`` (a tenth of the rows) the number of level-1 rows. A level-1 row has
    no parent; every other row's parent is a uniformly drawn row of the level above. The levels
    are available to a ``self_ref_field`` column of the same table.
    """

    name = "self_referencing"

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> Mapping[str, pa.Array]:
        engine = engine_of(ctx, "self_referencing")
        pk_column = str(spec.get("pk_column") or "")
        if not pk_column or pk_column not in ctx.columns:
            raise StrategyError(
                f"self_referencing strategy for {where(ctx)}: pk_column '{pk_column}' not found "
                "in the table's earlier columns. Ensure the key column is defined before this one"
            )
        raw_levels = spec.get("levels")
        levels = int(spec.get("max_depth", 3) if raw_levels is None else raw_levels)
        if levels < 1:
            raise StrategyError(f"self_referencing on {where(ctx)}: levels must be at least 1")
        n = engine.row_counts[ctx.table]
        root_count = int(spec.get("root_count", max(1, n // 10)))
        starts, ends = level_bounds(n, levels, root_count)
        roots = max(1, min(root_count, n // levels))

        rows = np.arange(ctx.row_start, ctx.row_start + ctx.n_rows, dtype=np.int64)
        level = np.searchsorted(np.asarray(starts, dtype=np.int64), rows, side="right")
        above = np.maximum(level - 2, 0)
        first = np.asarray(starts, dtype=np.int64)[above]
        size = np.asarray(ends, dtype=np.int64)[above] - first
        empty = size <= 0  # no rows in the level above: fall back to the roots
        first = np.where(empty, 0, first)
        size = np.where(empty, roots, size)
        u = stream(ctx, "parent").uniform(ctx.row_start, ctx.n_rows)
        index: Ints = first + np.minimum((u * size).astype(np.int64), size - 1)
        pool = parent_pool(ctx, ctx.table, pk_column, "self_referencing")
        parent = take_keys(pool, index, level == 1)
        return {ctx.column: parent, sr_key(ctx.table, "level"): pa.array(level.astype(np.int64))}


class SelfRefField:
    """A field ``self_referencing`` worked out for the same table (``spec['field']``, ``level`` by
    default): the 1-based level of the row."""

    name = "self_ref_field"

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        field = str(spec.get("field", "level"))
        key = sr_key(ctx.table, field)
        if key not in ctx.columns:
            raise StrategyError(
                f"self_ref_field could not find '{key}' for {where(ctx)}. Ensure a "
                "self_referencing column of this table is defined before it, and that "
                f"'{field}' is one of its fields (level)"
            )
        return ctx.columns[key]


__all__ = ["SHAPE_API", "SelfRefField", "SelfReferencing", "level_bounds", "sr_key"]
