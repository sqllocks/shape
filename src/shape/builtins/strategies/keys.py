"""Foreign keys (P4-04d): ``foreign_key``, ``composite_foreign_key`` and ``composite_fk_field``.

All three are row addressed: the parent a row points at is a function of the run seed, the table,
the column and the row, so a chunk read in any order, or split any way, gives the same keys.
Parents come from the engine's key pools (``Engine.key_pool``), never from the rows generated so
far in a chunk.
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
from shape.generation.fanout import FanOut
from shape.generation.kernel_relational import cap_per_parent
from shape.generation.strategy_kit import StrategyError, require, stream, where
from shape.plugins.api.v1 import GenerationContext

from ._relational import (
    BLOCK_ROWS,
    Ints,
    engine_of,
    parent_pool,
    pareto_index,
    positive,
    take_keys,
    whole_column,
    zipf_index,
)

SHAPE_API = "1.0"

CFO_PREFIX = "_cfo_"


def cfo_key(source_column: str, ref_column: str) -> str:
    """The internal name under which ``composite_foreign_key`` hands a parent column to the
    ``composite_fk_field`` columns of its row."""
    return f"{CFO_PREFIX}{source_column}__{ref_column}"


def _params(spec: Mapping[str, Any]) -> dict[str, Any]:
    """``params`` with the top-level ``alpha`` and ``max_per_parent`` as fallbacks (``params``
    wins)."""
    params = dict(spec.get("params") or {})
    for key in ("alpha", "max_per_parent"):
        if key in spec and key not in params:
            params[key] = spec[key]
    return params


def _indices(
    distribution: str,
    params: Mapping[str, Any],
    pool: int,
    row_start: int,
    n_rows: int,
    ctx: GenerationContext,
) -> Ints:
    """Parent row numbers (``0 .. pool - 1``) of ``n_rows`` rows from the ``fk`` stream: uniform,
    ``zipf`` or ``pareto`` (anything else is uniform)."""
    u = stream(ctx, "fk").uniform(row_start, n_rows)
    if distribution == "zipf":
        return zipf_index(u, pool, positive(params, "alpha", 1.5, ctx))
    if distribution == "pareto":
        return pareto_index(u, pool, positive(params, "alpha", 1.2, ctx))
    return np.minimum((u * pool).astype(np.int64), pool - 1)


class ForeignKey:
    """Values of a parent table's key column (``spec['ref']`` = ``"table.column"``).

    ``distribution``: ``uniform`` (default), ``zipf`` (parameter ``alpha``, 1.5) or ``pareto``
    (``alpha`` 1.2, optionally ``max_per_parent``: no parent gets more rows than that), with the
    parameters under ``params`` or at the top level. ``constrained_by``: another column of this
    table; the key is drawn from the parent rows whose column of the same name has the same value
    (a null when there is none and the column is nullable, else any parent). ``sample_rate`` (with
    an optional ``filter``, ``"column = 'value'"``): the rows take the parents of one random
    sample, without replacement, of that share of the parent rows. A reference to the table's own
    key draws uniformly among all its rows (``distribution`` does not apply).
    """

    name = "foreign_key"

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        ref = str(require(spec, "ref", ctx, "foreign_key"))
        if "." not in ref:
            raise StrategyError(
                f"foreign_key strategy requires 'ref' in format 'table.column', got '{ref}' "
                f"for column {where(ctx)}"
            )
        ref_table, ref_column = ref.split(".", 1)
        if ctx.n_rows == 0:  # an empty child needs no parent row, even of an empty parent (#220)
            pool = parent_pool(ctx, ref_table, ref_column, "foreign_key")
            return pool.take(np.zeros(0, dtype=np.int64))
        params = _params(spec)
        constrained_by = spec.get("constrained_by")
        if constrained_by and constrained_by in ctx.columns:
            return self._constrained(spec, ctx, ref_table, ref_column, str(constrained_by))
        if spec.get("sample_rate") is not None:
            return self._sampled(spec, ctx, ref_table, ref_column)
        pool = parent_pool(ctx, ref_table, ref_column, "foreign_key")
        if len(pool) == 0:
            raise StrategyError(f"foreign_key on {where(ctx)}: table '{ref_table}' has no rows")
        fan_out = spec.get("fan_out")
        if fan_out is not None:
            if not isinstance(fan_out, Mapping):
                raise StrategyError(f"fan_out must be a mapping for {where(ctx)}")
            fan = engine_of(ctx, "foreign_key").cached(
                ("fk-fan-out", ctx.table, ctx.column, len(pool), tuple(sorted(fan_out.items()))),
                lambda: FanOut.from_spec(len(pool), dict(fan_out)),
            )
            return pool.take(fan.draw(stream(ctx, "fan"), ctx.row_start, ctx.n_rows))
        distribution = "uniform" if ref_table == ctx.table else spec.get("distribution", "uniform")
        if distribution == "pareto" and params.get("max_per_parent") is not None:
            index = self._capped(distribution, params, len(pool), ctx)
            return pool.take(index[ctx.row_start : ctx.row_start + ctx.n_rows])
        index = _indices(distribution, params, len(pool), ctx.row_start, ctx.n_rows, ctx)
        return pool.take(index)

    @staticmethod
    def _capped(
        distribution: str, params: Mapping[str, Any], pool: int, ctx: GenerationContext
    ) -> Ints:
        """The parent of every row of the table with at most ``max_per_parent`` rows per parent:
        a row-sequential pass, so it is computed once over the whole table."""
        engine = engine_of(ctx, "foreign_key")
        cap = int(params["max_per_parent"])
        if cap < 1:
            raise StrategyError(f"max_per_parent must be positive for {where(ctx)}, got {cap}")

        def build() -> Ints:
            total = engine.row_counts[ctx.table]
            parts = [
                _indices(distribution, params, pool, s, min(BLOCK_ROWS, total - s), ctx)
                for s in range(0, total, BLOCK_ROWS)
            ]
            base = np.concatenate(parts) if parts else np.empty(0, dtype=np.int64)
            return cap_per_parent(base, pool, cap, stream(ctx, "cap"))

        key = ("fk-cap", ctx.table, ctx.column, distribution, cap, tuple(sorted(params.items())))
        return engine.cached(key, build)

    @staticmethod
    def _constrained(
        spec: Mapping[str, Any],
        ctx: GenerationContext,
        ref_table: str,
        ref_column: str,
        constrained_by: str,
    ) -> pa.Array:
        engine = engine_of(ctx, "foreign_key")
        parent = engine.schema.tables.get(ref_table)
        pool = parent_pool(ctx, ref_table, ref_column, "foreign_key")
        if parent is None or constrained_by not in parent.columns:
            index = _indices("uniform", {}, len(pool), ctx.row_start, ctx.n_rows, ctx)
            return pool.take(index)
        groups = engine.cached(
            ("fk-groups", ref_table, constrained_by),
            lambda: _Groups.of(whole_column(ctx, ref_table, constrained_by, "foreign_key")),
        )
        match, group = groups.find(ctx.columns[constrained_by])
        u = stream(ctx, "pick").uniform(ctx.row_start, ctx.n_rows)
        size = groups.starts[group + 1] - groups.starts[group]
        within = np.minimum((u * np.maximum(size, 1)).astype(np.int64), np.maximum(size - 1, 0))
        picked = groups.rows[
            np.minimum(groups.starts[group] + within, max(len(groups.rows) - 1, 0))
        ]
        anywhere = _indices("uniform", {}, len(pool), ctx.row_start, ctx.n_rows, ctx)
        column = getattr(ctx, "column_def", None)
        nullable = bool(column.nullable) if column is not None else False
        index = np.where(match, picked, anywhere)
        return take_keys(pool, index, ~match if nullable else None)

    @staticmethod
    def _sampled(
        spec: Mapping[str, Any], ctx: GenerationContext, ref_table: str, ref_column: str
    ) -> pa.Array:
        engine = engine_of(ctx, "foreign_key")
        rate = positive(spec, "sample_rate", 0.0, ctx)
        text = str(spec.get("filter", ""))
        pool = parent_pool(ctx, ref_table, ref_column, "foreign_key")

        def build() -> Ints:
            rows = np.arange(len(pool), dtype=np.int64)
            if "=" in text:
                column, _, value = text.partition("=")
                found = whole_column(ctx, ref_table, column.strip(), "foreign_key")
                same = pc.equal(pc.cast(found, pa.string()), value.strip().strip("'\""))
                rows = np.flatnonzero(np.asarray(arrow_numpy(arrow_fill_null(same, False)))).astype(
                    np.int64
                )
            if len(rows) == 0:
                raise StrategyError(f"foreign_key on {where(ctx)}: no row of '{ref_table}' matches")
            take = max(1, int(len(rows) * rate))
            order = np.argsort(stream(ctx, "sample").uniform(0, len(rows)), kind="stable")
            return rows[order[:take]]

        chosen = engine.cached(("fk-sample", ctx.table, ctx.column, text, rate), build)
        at = np.arange(ctx.row_start, ctx.row_start + ctx.n_rows, dtype=np.int64) % len(chosen)
        return pool.take(chosen[at])


class _Groups:
    """The rows of a table grouped by the value of one column (nulls belong to no group)."""

    __slots__ = ("lowest", "rows", "slot", "starts", "values")

    def __init__(self, values: pa.Array, rows: Ints, starts: Ints) -> None:
        self.values, self.rows, self.starts = values, rows, starts
        self.lowest = 0
        self.slot: Ints | None = None
        if pa.types.is_signed_integer(values.type) and len(values) and not values.null_count:
            numbers = np.asarray(arrow_numpy(values), dtype=np.int64)
            low, high = int(numbers.min()), int(numbers.max())
            if high - low < max(4 * len(numbers), 1024):  # keys close together: a table lookup
                self.lowest = low
                self.slot = np.full(high - low + 1, -1, dtype=np.int64)
                self.slot[numbers - low] = np.arange(len(numbers), dtype=np.int64)

    def find(self, want: pa.Array) -> tuple[npt.NDArray[np.bool_], Ints]:
        """For each value of ``want``: whether it is a group, and the group number (0 if not)."""
        if self.slot is not None and pa.types.is_signed_integer(want.type):
            null = np.asarray(arrow_numpy(want.is_null()), dtype=np.bool_)
            numbers = np.asarray(arrow_numpy(arrow_fill_null(want, 0)))
            at = numbers.astype(np.int64) - self.lowest
            inside = (at >= 0) & (at < len(self.slot)) & ~null
            group = self.slot[np.where(inside, at, 0)]
            match = inside & (group >= 0)
            return match, np.where(match, group, 0)
        found = pc.index_in(want, value_set=self.values.cast(want.type, safe=False))
        match = np.asarray(arrow_numpy(pc.is_valid(found)), dtype=np.bool_)
        group = np.asarray(arrow_numpy(arrow_fill_null(found, 0)), dtype=np.int64)
        return match, group

    @classmethod
    def of(cls, column: pa.Array) -> _Groups:
        encoded = pc.dictionary_encode(column)
        codes = np.asarray(arrow_numpy(arrow_fill_null(encoded.indices, -1)), dtype=np.int64)
        n_groups = len(encoded.dictionary)
        valid = np.flatnonzero(codes >= 0)
        rows = valid[np.argsort(codes[valid], kind="stable")].astype(np.int64)
        starts = np.zeros(n_groups + 1, dtype=np.int64)
        np.cumsum(np.bincount(codes[valid], minlength=n_groups), out=starts[1:])
        return cls(encoded.dictionary, rows, starts)


class CompositeForeignKey:
    """A row of a parent table with a composite key: ``ref_table`` and ``ref_columns``.

    One parent row is drawn per child row (``distribution`` ``uniform`` or ``zipf``, with
    ``params.alpha``) and every ``ref_columns`` value of it is handed to the row: the column
    itself gets the first, and each ``composite_fk_field`` column names the one it reads.
    """

    name = "composite_foreign_key"

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> Mapping[str, pa.Array]:
        engine = engine_of(ctx, "composite_foreign_key")
        ref_table = str(spec.get("ref_table") or "")
        ref_columns = list(spec.get("ref_columns") or [])
        if not ref_table:
            raise StrategyError(f"composite_foreign_key on {where(ctx)}: 'ref_table' is required")
        if not ref_columns:
            raise StrategyError(
                f"composite_foreign_key on {where(ctx)}: 'ref_columns' must be a non-empty list"
            )
        if ref_table not in engine.schema.tables:
            raise StrategyError(
                f"composite_foreign_key on {where(ctx)}: there is no table '{ref_table}'"
            )
        parent = engine.generate_table(ref_table)
        for name in ref_columns:
            if name not in parent.column_names:
                raise StrategyError(
                    f"composite_foreign_key on {where(ctx)}: "
                    f"ref_column '{name}' not found in '{ref_table}'"
                )
        if ctx.n_rows == 0:  # an empty child needs no parent row, even of an empty parent (#220)
            taken = arrow_array(np.zeros(0, dtype=np.int64))
        elif parent.num_rows == 0:
            raise StrategyError(f"composite_foreign_key on {where(ctx)}: '{ref_table}' has 0 rows")
        else:
            index = _indices(
                "zipf" if spec.get("distribution") == "zipf" else "uniform",
                dict(spec.get("params") or {}),
                parent.num_rows,
                ctx.row_start,
                ctx.n_rows,
                ctx,
            )
            taken = arrow_array(index)
        values = {name: pc.take(parent.column(name), taken) for name in ref_columns}
        out: dict[str, pa.Array] = {ctx.column: values[ref_columns[0]]}
        out.update({cfo_key(ctx.column, name): arr for name, arr in values.items()})
        return out


class CompositeFkField:
    """One column of the parent row ``composite_foreign_key`` drew for this row:
    ``source_column`` (the composite_foreign_key column, earlier in the table) and
    ``ref_column``."""

    name = "composite_fk_field"

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        source = spec.get("source_column") or ""
        ref_column = spec.get("ref_column") or ""
        if not source or not ref_column:
            raise StrategyError(
                f"composite_fk_field on {where(ctx)}: 'source_column' and 'ref_column' are required"
            )
        key = cfo_key(str(source), str(ref_column))
        if key not in ctx.columns:
            raise StrategyError(
                f"composite_fk_field on {where(ctx)}: '{key}' not found. Ensure the "
                f"composite_foreign_key column '{source}' is defined, with '{ref_column}' among "
                "its ref_columns"
            )
        return ctx.columns[key]


__all__ = ["SHAPE_API", "CompositeFkField", "CompositeForeignKey", "ForeignKey", "cfo_key"]
