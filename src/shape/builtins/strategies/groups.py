"""Strategies that look at a whole group of rows (P4-04d): ``first_per_parent`` and ``scd2``.

Which row is the first of its parent, or the latest version of its business key, depends on the
rows before it, so both are row-sequential: the whole table's column is read once, the
row-sequential kernel (``generation/kernel_relational.py``, native or twin) works out every row,
and the result is kept for the engine. A chunk, read in any order, takes its slice.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.generation.arrowkit import array as arrow_array
from shape.generation.arrowkit import fill_null as arrow_fill_null
from shape.generation.arrowkit import to_numpy as arrow_numpy
from shape.generation.kernel_relational import first_flags, group_order, scd2_offsets
from shape.generation.strategy_kit import StrategyError, require, stream, where
from shape.plugins.api.v1 import GenerationContext

from ._relational import Ints, engine_of, whole_column

SHAPE_API = "1.0"

_US_PER_DAY = 86_400_000_000
_ROLES = ("effective_date", "end_date", "is_current", "version")


def group_codes(column: pa.Array, *, nulls_are_a_group: bool) -> Ints:
    """Dense group ids of the values of ``column`` in order of first appearance. A null is its own
    group, or -1 (no group) when ``nulls_are_a_group`` is false."""
    encoded = pc.dictionary_encode(column, null_encoding="encode" if nulls_are_a_group else "mask")
    return np.asarray(arrow_numpy(arrow_fill_null(encoded.indices, -1)), dtype=np.int64)


class FirstPerParent:
    """``True`` for the first row of each value of ``parent_column`` and ``False`` for the rest
    (``default`` swaps the two: ``"default": false`` marks the first row ``False``). Nulls in the
    parent column count as one value."""

    name = "first_per_parent"
    generator_version = 1

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        parent_column = str(require(spec, "parent_column", ctx, "first_per_parent"))
        if parent_column not in ctx.columns:
            raise StrategyError(
                f"first_per_parent: parent_column '{parent_column}' has not been generated yet "
                f"for {where(ctx)}. Ensure foreign key columns come before first_per_parent ones"
            )
        engine = engine_of(ctx, "first_per_parent")

        def build() -> npt.NDArray[np.bool_]:
            values = whole_column(ctx, ctx.table, parent_column, "first_per_parent", keep=False)
            return first_flags(group_codes(values, nulls_are_a_group=True))

        flags = engine.cached(("first-per-parent", ctx.table, parent_column), build)
        first = flags[ctx.row_start : ctx.row_start + ctx.n_rows]
        return arrow_array(first if bool(spec.get("default", True)) else ~first, type=pa.bool_())


def _date_range(ctx: GenerationContext) -> tuple[dt.date, int]:
    """The model's ``date_range`` as (start date, days to the end)."""
    span = engine_of(ctx, "scd2").schema.model.date_range or {}
    start = dt.date.fromisoformat(str(span.get("start", "2022-01-01"))[:10])
    end = dt.date.fromisoformat(str(span.get("end", "2024-12-31"))[:10])
    return start, (end - start).days


def _micros(column: pa.Array) -> Ints:
    """Microseconds since the epoch of a date or timestamp column (nulls sort first)."""
    stamped = pc.cast(column, pa.timestamp("us"))
    values = arrow_fill_null(pc.cast(stamped, pa.int64()), np.iinfo(np.int64).min)
    return np.asarray(arrow_numpy(values), dtype=np.int64)


class Scd2:
    """The versioning columns of a type 2 slowly changing dimension, grouped by the
    ``business_key`` column (earlier in the table). ``role`` picks the column:

    * ``effective_date``: the versions of a key get increasing dates in the model's date range,
      at least ``min_gap_days`` (1) apart, in row order;
    * ``end_date``: the next version's effective date minus ``min_gap_days``; null for the latest.
      ``effective_date_column`` (default ``effective_date``) names the date column;
    * ``is_current``: ``True`` for the latest version of each key;
    * ``version``: 1, 2, ... in effective-date order.

    Rows with a null business key get nulls. Dates are ``timestamp[us]`` at midnight.
    """

    name = "scd2"
    generator_version = 1

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        role = str(spec.get("role", "effective_date"))
        business_key = str(spec.get("business_key") or "")
        if not business_key:
            raise StrategyError(f"scd2 strategy requires 'business_key' for column {where(ctx)}")
        if business_key not in ctx.columns:
            raise StrategyError(
                f"scd2: business_key '{business_key}' must be generated before the scd2 columns "
                f"({where(ctx)})"
            )
        if role not in _ROLES:
            raise StrategyError(f"scd2: unknown role '{role}' for {where(ctx)}")
        min_gap = int(spec.get("min_gap_days", 1))
        if min_gap < 0:
            raise StrategyError(f"scd2: min_gap_days must not be negative ({where(ctx)})")
        window = slice(ctx.row_start, ctx.row_start + ctx.n_rows)
        engine = engine_of(ctx, "scd2")
        codes = engine.cached(
            ("scd2-codes", ctx.table, business_key),
            lambda: group_codes(
                whole_column(ctx, ctx.table, business_key, "scd2", keep=False),
                nulls_are_a_group=False,
            ),
        )
        if role == "effective_date":
            start, total_days = _date_range(ctx)
            offsets = engine.cached(
                ("scd2-offsets", ctx.table, ctx.column, business_key, total_days, min_gap),
                lambda: scd2_offsets(codes, total_days, min_gap, stream(ctx, "scd2")),
            )[window]
            micros = (start.toordinal() - dt.date(1970, 1, 1).toordinal() + offsets) * _US_PER_DAY
            return arrow_array(micros, mask=offsets < 0, type=pa.int64()).cast(pa.timestamp("us"))

        eff_column = str(spec.get("effective_date_column", "effective_date"))
        keys: Ints | None = None
        if eff_column in ctx.columns:
            keys = engine.cached(
                ("scd2-eff", ctx.table, eff_column),
                lambda: _micros(whole_column(ctx, ctx.table, eff_column, "scd2", keep=False)),
            )
        if role == "end_date" and keys is None:
            raise StrategyError(
                f"scd2 end_date for {where(ctx)} requires the effective date column "
                f"'{eff_column}' to be generated first"
            )
        order_keys = keys if keys is not None else np.zeros_like(codes)
        rank, size, following = engine.cached(
            ("scd2-order", ctx.table, business_key, eff_column if keys is not None else None),
            lambda: group_order(codes, order_keys),
        )
        missing = rank[window] < 0
        if role == "version":
            return arrow_array(rank[window] + 1, mask=missing, type=pa.int64())
        if role == "is_current":
            return arrow_array(rank[window] == size[window] - 1, mask=missing, type=pa.bool_())
        after = following[window]
        end = order_keys[np.maximum(after, 0)] - min_gap * _US_PER_DAY
        return arrow_array(end, mask=after < 0, type=pa.int64()).cast(pa.timestamp("us"))


__all__ = ["SHAPE_API", "FirstPerParent", "Scd2", "group_codes"]
