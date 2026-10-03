"""Built-in strategies ``derived`` and ``computed``.

``derived`` makes a column from another one, in the same row or in a parent row:

    {"strategy": "derived", "source": "start_date", "rule": "add_days",
     "params": {"distribution": "uniform", "min": 3, "max": 30}}
    {"strategy": "derived", "source": "order.order_date", "via": "order_id", "rule": "add_days",
     "params": {"distribution": "log_normal", "mean": 2.0, "sigma": 0.8, "min": 1, "max": 90}}

``computed`` marks a column whose value is an aggregate of child rows (or a value copied from the
parent). The engine writes a null placeholder and ``shape.generation.compute`` back-fills it once
every table exists (``docs/GENERATION_ENGINE.md``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.generation.arrowkit import array as arrow_array
from shape.generation.arrowkit import fill_null as arrow_fill_null
from shape.generation.arrowkit import to_numpy as arrow_numpy
from shape.generation.engine import RangeKeys
from shape.generation.strategy_kit import StrategyError, stream, where
from shape.plugins.api.v1 import GenerationContext

SHAPE_API = "1.0"

_ISO_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d")
_TIMESTAMP_UNITS = {"s": 1, "ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}
_SECONDS_PER_DAY = 86_400


def sample_days(
    ctx: GenerationContext, params: Mapping[str, Any], purpose: str = "days"
) -> npt.NDArray[np.int64]:
    """Whole-day offsets for the rows of ``ctx``. ``distribution`` (default ``uniform``) is
    ``uniform`` (``min``, ``max``), ``log_normal`` (``mean``, ``sigma`` of the log) or ``normal``
    (``mean``, ``std_dev``), the last two clipped to ``[min, max]``; any other name is
    ``uniform``. ``min`` and ``max`` default to 1 and 30. The draw is rounded to whole days."""
    dist = params.get("distribution", "uniform")
    low, high = float(params.get("min", 1)), float(params.get("max", 30))
    s = stream(ctx, purpose)
    if dist == "log_normal":
        mean, sigma = float(params.get("mean", 2.0)), float(params.get("sigma", 0.8))
        days = np.clip(np.exp(mean + sigma * s.normal(ctx.row_start, ctx.n_rows)), low, high)
    elif dist == "normal":
        mean, std = float(params.get("mean", 10.0)), float(params.get("std_dev", 3.0))
        days = np.clip(mean + std * s.normal(ctx.row_start, ctx.n_rows), low, high)
    else:
        days = low + (high - low) * s.uniform(ctx.row_start, ctx.n_rows)
    whole: npt.NDArray[np.int64] = np.rint(days).astype(np.int64)
    return whole


def _combine(column: pa.Array | pa.ChunkedArray) -> pa.Array:
    return column.combine_chunks() if isinstance(column, pa.ChunkedArray) else column


def _timestamps(values: pa.Array, ctx: GenerationContext) -> pa.Array:
    """``values`` as a date or timestamp array: ISO text is parsed (unparsable text is null)."""
    t = values.type
    if pa.types.is_timestamp(t) or pa.types.is_date32(t):
        return values
    if pa.types.is_string(t) or pa.types.is_large_string(t):
        parsed = [pc.strptime(values, f, "us", error_is_null=True) for f in _ISO_FORMATS]
        return pc.coalesce(*parsed)
    raise StrategyError(
        f"derived add_days for {where(ctx)} needs a date, timestamp or ISO text source, not {t}"
    )


def _add_days(values: pa.Array, days: npt.NDArray[np.int64]) -> pa.Array:
    t = values.type
    if pa.types.is_date32(t):
        unit_per_day = 1
        base = pc.cast(values, pa.int32()).cast(pa.int64())
    else:
        unit_per_day = _SECONDS_PER_DAY * _TIMESTAMP_UNITS[t.unit]
        base = pc.cast(values, pa.int64())
    mask = arrow_numpy(base.is_null()) if base.null_count else None
    shifted = np.asarray(arrow_numpy(arrow_fill_null(base, 0))) + days * unit_per_day
    out = arrow_array(shifted, mask=mask)
    return out.cast(pa.int32()).cast(t) if pa.types.is_date32(t) else out.cast(t)


class Derived:
    """A column derived from ``spec['source']``.

    * ``source`` is a column generated earlier in this table, or ``"table.column"`` together with
      ``via``, the name of this table's foreign-key column to that table (its key column carries
      the same name in the parent, unless the foreign key says otherwise). The parent's column is
      generated in full once and read by key; a missing parent gives null.
    * ``rule`` (alias ``operation``) is ``copy`` (the default: the source value) or ``add_days``
      (source plus a whole number of days from ``params``: see :func:`sample_days`). The result
      has the source's date or timestamp type; ISO text becomes ``timestamp[us]``; a null stays
      null. ``days: N`` at the top of the spec means ``add_days`` of exactly ``N``.
    """

    name = "derived"
    generator_version = 1

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        source = str(spec.get("source", ""))
        via = spec.get("via")
        rule = spec.get("rule", spec.get("operation", "copy"))
        params = dict(spec.get("params") or {})
        if "days" in spec:
            if rule == "copy":
                rule = "add_days"
            params.setdefault("distribution", "uniform")
            params.setdefault("min", spec["days"])
            params.setdefault("max", spec["days"])
        if rule not in ("copy", "add_days"):
            raise StrategyError(
                f"derived strategy: unknown rule {rule!r} for column {where(ctx)}. "
                "Supported rules: copy, add_days"
            )
        values = self._source_values(source, via, ctx)
        if rule == "copy":
            return values
        return _add_days(_timestamps(values, ctx), sample_days(ctx, params))

    def _source_values(self, source: str, via: Any, ctx: GenerationContext) -> pa.Array:
        if "." in source and via:
            return self._parent_values(source, str(via), ctx)
        if source in ctx.columns:
            return _combine(ctx.columns[source])
        raise StrategyError(
            f"derived strategy for {where(ctx)}: source {source!r} not found. Ensure the source "
            "column appears before it in the schema."
        )

    @staticmethod
    def _parent_values(source: str, via: str, ctx: GenerationContext) -> pa.Array:
        parent_name, parent_column = source.split(".", 1)
        engine = getattr(ctx, "engine", None)
        if engine is None:
            raise StrategyError(f"derived strategy for {where(ctx)} needs the generation engine")
        if via not in ctx.columns:
            raise StrategyError(
                f"derived strategy for {where(ctx)}: via column {via!r} is not generated before it"
            )
        tables = engine.schema.tables
        if parent_name not in tables:
            raise StrategyError(f"derived strategy for {where(ctx)}: no table {parent_name!r}")
        link = tables[ctx.table].columns.get(via)
        key = via
        if link is not None and link.fk_ref_table == parent_name and link.fk_ref_column:
            key = link.fk_ref_column
        parent = engine.generate_table(parent_name)
        for needed in (key, parent_column):
            if needed not in parent.column_names:
                raise StrategyError(
                    f"derived strategy for {where(ctx)}: table {parent_name!r} has no column "
                    f"{needed!r}"
                )
        wanted = _combine(ctx.columns[via])
        pool = engine.key_pool(parent_name) if tables[parent_name].primary_key == [key] else None
        if isinstance(pool, RangeKeys) and pool.step > 0 and pa.types.is_integer(wanted.type):
            # A sequence key is its own row index: no hash table over the parent's keys.
            offset = (
                np.asarray(arrow_numpy(arrow_fill_null(pc.cast(wanted, pa.int64()), -1)))
                - pool.start
            )
            row = offset // pool.step
            ok = (offset >= 0) & (offset % pool.step == 0) & (row < pool.count)
            position = arrow_array(np.where(ok, row, 0), mask=~ok | arrow_numpy(wanted.is_null()))
        else:
            position = pc.index_in(wanted, value_set=parent.column(key).combine_chunks())
        return pc.take(parent.column(parent_column).combine_chunks(), position)


class Computed:
    """A column back-filled from other tables by the compute phase. ``generate`` returns the null
    placeholder the engine would write; the phase replaces it (rules ``sum_children``,
    ``count_children``, ``avg_children``, ``min_children``, ``max_children`` and
    ``lookup_parent``, see ``shape.generation.compute``)."""

    name = "computed"
    generator_version = 1

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        return pa.nulls(ctx.n_rows, pa.float64())


__all__ = ["SHAPE_API", "Computed", "Derived", "sample_days"]
