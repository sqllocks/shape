"""Anomalies in a stream through the ``shape.chaos`` mutator protocol (P5-01).

``--anomaly-fraction F`` marks each row an anomaly with probability ``F`` (a row-addressed draw,
so the same rows are chosen on every run and after a restart), hands the chosen rows to the
mutators, and puts the mutated rows back where they were. The flag is never ignored: a fraction
above zero with no usable mutator is an error.

A mutator used here must keep the row count and the schema of the batch it is given, because an
event's ``_shape_seq`` is its row's position. ``ValueAnomalyMutator`` is the default: it replaces
one value of each row it gets with an outlier or a null. Any ``shape.chaos`` plugin can be named
instead (``--anomaly-mutator NAME``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.errors import ShapeError, ShapeSchemaError
from shape.generation.rng import RowStream, stream_key
from shape.plugins.api.v1 import ChaosMutator, ChaosReport

DEFAULT_MUTATOR = "value-anomaly"
_OUTLIER_FACTORS = np.array([-1.0, 100.0, 1000.0])


def _is_key_like(name: str) -> bool:
    low = name.lower()
    return low == "id" or low.endswith("_id")


class ValueAnomalyMutator:
    """One value per row becomes an outlier (a number) or empty (a string), or null."""

    name = DEFAULT_MUTATOR

    def mutate(self, batch: pa.RecordBatch, seed: int) -> tuple[pa.RecordBatch, ChaosReport]:
        n = batch.num_rows
        eligible = [
            i
            for i, f in enumerate(batch.schema)
            if not _is_key_like(f.name)
            and (
                pa.types.is_integer(f.type)
                or pa.types.is_floating(f.type)
                or pa.types.is_string(f.type)
                or pa.types.is_large_string(f.type)
            )
        ]
        if n == 0 or not eligible:
            return batch, ChaosReport(self.name, 0, {})
        rng = np.random.Generator(np.random.PCG64(seed & 0xFFFFFFFFFFFFFFFF))
        target = rng.integers(0, len(eligible), n)
        kind = rng.integers(0, 4, n)  # 0: null, 1..3: outlier factor / empty string
        columns = list(batch.columns)
        per_column: dict[str, int] = {}
        for slot, ci in enumerate(eligible):
            rows = target == slot
            if not rows.any():
                continue
            col = columns[ci]
            t = col.type
            nulls = rows & (kind == 0)
            if pa.types.is_integer(t) or pa.types.is_floating(t):
                values = col.to_numpy(zero_copy_only=False).astype(np.float64)
                factor = _OUTLIER_FACTORS[np.maximum(kind - 1, 0)]
                values = np.where(rows & (kind > 0), values * factor, values)
                if pa.types.is_integer(t):
                    info = np.iinfo(t.to_pandas_dtype())
                    values = np.clip(np.nan_to_num(values), info.min, info.max)
                    out = pa.array(values.astype(t.to_pandas_dtype()), t)
                else:
                    out = pa.array(values, t)
            else:
                out = pc.if_else(pa.array(rows & (kind > 0)), pa.scalar("", t), col)
            if nulls.any():
                out = pc.if_else(pa.array(nulls), pa.scalar(None, t), out)
            columns[ci] = out
            per_column[batch.schema.field(ci).name] = int(rows.sum())
        return (
            pa.RecordBatch.from_arrays(columns, schema=batch.schema),
            ChaosReport(self.name, n, {"columns": per_column}),
        )


def resolve_mutators(names: Sequence[str] = ()) -> list[ChaosMutator]:
    """The mutators to use: ``names`` (the default mutator, or ``shape.chaos`` plugins), or just
    the default mutator when ``names`` is empty."""
    if not names:
        return [ValueAnomalyMutator()]
    out: list[ChaosMutator] = []
    for name in names:
        if name == DEFAULT_MUTATOR:
            out.append(ValueAnomalyMutator())
            continue
        from shape.plugins.host import PluginLoadError, default_host

        try:
            out.append(default_host().get("shape.chaos", name))
        except (KeyError, PluginLoadError) as exc:
            known = ", ".join([DEFAULT_MUTATOR, *default_host().names("shape.chaos")])
            raise ShapeError(f"anomaly mutator {name!r}: {exc} (available: {known})") from exc
    return out


@dataclass
class AnomalyStats:
    rows_selected: int = 0
    rows_affected: dict[str, int] = field(default_factory=dict)


class AnomalyInjector:
    """Applies mutators to a fraction of the rows of each block."""

    def __init__(
        self, fraction: float, mutators: Sequence[ChaosMutator], seed: int
    ) -> None:
        if not 0.0 <= fraction <= 1.0:
            raise ValueError("anomaly fraction must be between 0 and 1")
        if fraction > 0 and not mutators:
            raise ShapeError("an anomaly fraction above 0 needs at least one mutator")
        self.fraction = fraction
        self.mutators = list(mutators)
        self.seed = seed
        self.stats = AnomalyStats()

    def apply(self, batch: pa.RecordBatch, table: str, row_start: int) -> pa.RecordBatch:
        """``batch`` (rows ``row_start ..`` of ``table``) with a fraction of its rows mutated."""
        if self.fraction <= 0 or batch.num_rows == 0:
            return batch
        u = RowStream(self.seed, table, "_anomaly", "select").uniform(row_start, batch.num_rows)
        chosen = np.asarray(u) < self.fraction
        k = int(chosen.sum())
        if k == 0:
            return batch
        mask = pa.array(chosen)
        sub = batch.filter(mask)
        mseed = stream_key(self.seed, table, "_anomaly", f"mutate/{row_start}") & 0x7FFFFFFFFFFFFFFF
        for m in self.mutators:
            sub, report = m.mutate(sub, mseed)
            if sub.num_rows != k:
                raise ShapeSchemaError(
                    f"mutator {m.name!r} changed the row count ({k} -> {sub.num_rows}); a stream "
                    "mutator must keep it, because an event's sequence number is its row position"
                )
            if sub.schema != batch.schema:
                raise ShapeSchemaError(f"mutator {m.name!r} changed the schema of the batch")
            self.stats.rows_affected[report.mutator] = (
                self.stats.rows_affected.get(report.mutator, 0) + report.rows_affected
            )
        self.stats.rows_selected += k
        columns: list[Any] = [
            pc.replace_with_mask(batch.column(i), mask, sub.column(i))
            for i in range(batch.num_columns)
        ]
        return pa.RecordBatch.from_arrays(columns, schema=batch.schema)
