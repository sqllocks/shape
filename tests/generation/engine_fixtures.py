"""Strategies for the engine tests that key their randomness by row, so a table does not depend
on how it is chunked (and one that does not, as a control)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pyarrow as pa

from shape.generation.rng import RowStream


class Seq:
    name = "sequence"

    def generate(self, spec: Any, ctx: Any) -> pa.Array:
        start, step = spec.get("start", 1), spec.get("step", 1)
        return pa.array(start + np.arange(ctx.row_start, ctx.row_start + ctx.n_rows) * step)


class RowUniform:
    """Uniform on [low, high), one stream per column, one word per row."""

    name = "distribution"

    def generate(self, spec: Any, ctx: Any) -> pa.Array:
        u = RowStream(ctx.seed, ctx.table, ctx.column, "v").uniform(ctx.row_start, ctx.n_rows)
        return pa.array(spec["low"] + u * (spec["high"] - spec["low"]))


class RowChoice:
    name = "weighted_enum"

    def generate(self, spec: Any, ctx: Any) -> pa.Array:
        u = RowStream(ctx.seed, ctx.table, ctx.column, "v").uniform(ctx.row_start, ctx.n_rows)
        values = spec["values"]
        return pa.array([values[int(x * len(values))] for x in u])


class RowFK:
    name = "foreign_key"

    def generate(self, spec: Any, ctx: Any) -> pa.Array:
        parent = spec["ref"].split(".")[0]
        pool = ctx.engine.key_pool(parent)
        u = RowStream(ctx.seed, ctx.table, ctx.column, "v").uniform(ctx.row_start, ctx.n_rows)
        return pool.take((u * len(pool)).astype(np.int64))


class Derived:
    """Twice another column of the same chunk."""

    name = "derived"

    def generate(self, spec: Any, ctx: Any) -> pa.Array:
        import pyarrow.compute as pc

        return pc.multiply(ctx.columns[spec["source"]], 2)


class ChunkKeyed:
    """Deliberately layout-dependent: the stream is keyed by the chunk index."""

    name = "chunk_keyed"

    def generate(self, spec: Any, ctx: Any) -> pa.Array:
        return pa.array(np.random.Philox(key=ctx.chunk).random_raw(ctx.n_rows).astype(np.float64))


STRATEGIES = {
    s.name: s for s in (Seq(), RowUniform(), RowChoice(), RowFK(), Derived(), ChunkKeyed())
}
