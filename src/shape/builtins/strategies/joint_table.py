"""Built-in strategy ``conditional_table``: a categorical column drawn given another column of
the same row, from a table of ``P(this | source)`` (a categorical joint table; #47).

``spec``: ``source_column`` (a column defined earlier in the table), ``table`` (a mapping from a
value of the source to a mapping from a value of this column to its probability or weight) and
``values`` (a mapping from value to weight, drawn from when the source's value has no entry or is
null: the column's own distribution). Weights of one source value are relative; a table may list
only its likeliest values. Row addressed: one uniform per row from the column's own stream. Like
``weighted_enum``, a column whose every value reads as a number is ``float64``, else a string.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.generation.arrowkit import array as arrow_array
from shape.generation.strategy_kit import StrategyError, require, stream, where
from shape.plugins.api.v1 import GenerationContext

SHAPE_API = "1.0"


def _cumulative(weights: Mapping[str, Any], ctx: GenerationContext) -> tuple[list[str], Any]:
    keys = [str(k) for k in weights]
    w = np.array([float(v) for v in weights.values()], dtype=np.float64)
    if len(w) == 0 or (w < 0).any() or not np.isfinite(w).all() or w.sum() <= 0:
        raise StrategyError(
            f"conditional_table needs non-negative weights with a positive sum ({where(ctx)})"
        )
    return keys, np.cumsum(w / w.sum())


class ConditionalTable:
    """A value drawn from the row's ``table[source value]`` (see the module docstring)."""

    name = "conditional_table"
    generator_version = 1

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        source = str(require(spec, "source_column", ctx, "conditional_table"))
        table = require(spec, "table", ctx, "conditional_table")
        fallback = require(spec, "values", ctx, "conditional_table")
        if not isinstance(table, Mapping) or not isinstance(fallback, Mapping):
            raise StrategyError(
                f"conditional_table 'table' and 'values' are objects ({where(ctx)})"
            )
        if source not in ctx.columns:
            raise StrategyError(
                f"conditional_table for {where(ctx)}: source column {source!r} is not generated "
                "yet; define it before this column"
            )
        src = pc.cast(ctx.columns[source], pa.string())
        d = pc.dictionary_encode(src)
        given = d.dictionary.to_pylist()
        codes = d.indices.to_numpy(zero_copy_only=False)
        valid = ~np.isnan(codes) if codes.dtype.kind == "f" else np.ones(len(codes), dtype=bool)
        codes = np.where(valid, codes, 0).astype(np.int64)
        u = stream(ctx, "v").uniform(ctx.row_start, ctx.n_rows)
        out_labels: list[str] = []
        label_ix: dict[str, int] = {}
        picked = np.zeros(ctx.n_rows, dtype=np.int64)

        def draw(rows: npt.NDArray[np.bool_], weights: Mapping[str, Any]) -> None:
            keys, cum = _cumulative(weights, ctx)
            at = np.minimum(np.searchsorted(cum, u[rows], side="right"), len(keys) - 1)
            ids = np.array([label_ix.setdefault(k, len(label_ix)) for k in keys], dtype=np.int64)
            picked[rows] = ids[at]

        covered = np.zeros(ctx.n_rows, dtype=bool)
        for i, label in enumerate(given):
            row = table.get(label)
            if not isinstance(row, Mapping) or not row:
                continue
            rows = valid & (codes == i)
            if rows.any():
                draw(rows, row)
                covered |= rows
        rest = ~covered
        if rest.any():
            draw(rest, fallback)
        out_labels = [k for k, _ in sorted(label_ix.items(), key=lambda kv: kv[1])]
        try:
            numbers = np.array([float(k) for k in out_labels], dtype=np.float64)
        except ValueError:
            return pc.take(arrow_array(out_labels, type=pa.string()), arrow_array(picked))
        return arrow_array(numbers[picked])


__all__ = ["SHAPE_API", "ConditionalTable"]
