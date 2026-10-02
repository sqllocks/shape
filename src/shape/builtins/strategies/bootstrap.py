"""Built-in strategy ``bootstrap``: columns resampled from a source table's rows.

Every ``bootstrap`` column of a table that names the same ``dataset`` takes the same randomly
chosen source row for each generated row (rows are drawn with replacement), so the columns keep
the source's joint distribution. A numeric column can be jittered with normal noise whose
standard deviation is a fraction of the source column's own. Row addressed: the choice of row
and the jitter are functions of the seed, table, column and row, never of the chunk.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.generation.rng import RowStream
from shape.generation.strategy_kit import StrategyError, require, stream, where
from shape.plugins.api.v1 import GenerationContext

from .reference_data import _dataset, _uniform_rows

SHAPE_API = "1.0"

DEFAULT_JITTER = 0.01


class Bootstrap:
    """``spec['field']`` of a source row of the dataset ``spec['dataset']`` drawn with
    replacement. ``spec['jitter']`` (default 0.01; 0 for none) is the jitter standard deviation as
    a fraction of the source column's standard deviation; it applies to integer and float fields
    only, which then become ``float64``. Nulls stay null."""

    name = "bootstrap"

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        dataset = str(require(spec, "dataset", ctx, "bootstrap"))
        field = str(require(spec, "field", ctx, "bootstrap"))
        ds = _dataset(dataset, ctx)
        if field not in ds.fields:
            raise StrategyError(
                f"Field {field!r} not found in dataset {dataset!r}. Available fields: "
                f"{list(ds.fields)} ({where(ctx)})"
            )
        jitter = float(spec.get("jitter", DEFAULT_JITTER))
        if not jitter >= 0:
            raise StrategyError(f"bootstrap 'jitter' must be 0 or more ({where(ctx)})")
        rows = RowStream(ctx.seed, ctx.table, f"bootstrap:{dataset}", "rows")
        column = ds.column(field)
        picked = pc.take(column, pa.array(_uniform_rows(rows, ctx.row_start, ctx.n_rows, len(ds))))
        t = column.type
        if jitter == 0 or not (pa.types.is_integer(t) or pa.types.is_floating(t)):
            return picked
        std = pc.stddev(column.cast(pa.float64()), ddof=1).as_py()
        if std is None or not std > 0:
            return picked
        noise = stream(ctx, "jitter").normal(ctx.row_start, ctx.n_rows) * (std * jitter)
        return pc.add(picked.cast(pa.float64()), pa.array(noise, type=pa.float64()))


__all__ = ["SHAPE_API", "Bootstrap"]
