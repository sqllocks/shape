"""The six chaos categories as ``shape.chaos`` plugins: schema, value, file, referential, temporal
and volume.

Each plugin applies its category once to the batch it is given, with the engine's defaults (the
``moderate`` intensity, and a day past the breaking-change day so that every mutation is
allowed), and reports what it did. Use :class:`shape.chaos.ChaosEngine` to schedule chaos over
days or to choose the intensity.

``schema``, ``value`` (through ``wrong_types``) and ``volume`` change the schema or the row count
by design. ``file`` corrupts the bytes of the batch's CSV rendering and returns them as a single
``payload`` binary column. ``referential`` sees one table, so only its duplicate-key mutation can
fire (orphan keys need a second table: call the mutator with a dict of tables).
"""

from __future__ import annotations

import io
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.csv as pacsv  # type: ignore[import-untyped]

from shape.chaos.categories import (
    FileChaosMutator,
    MutationEvent,
    Mutator,
    ReferentialChaosMutator,
    SchemaChaosMutator,
    TemporalChaosMutator,
    ValueChaosMutator,
    VolumeChaosMutator,
)
from shape.plugins.api.v1 import ChaosReport

SHAPE_API = "1.0"
_DAY = 365
_INTENSITY = 1.0


def _to_batch(table: pa.Table) -> pa.RecordBatch:
    if table.num_rows == 0:
        return pa.RecordBatch.from_arrays(
            [pa.array([], type=f.type) for f in table.schema], schema=table.schema
        )
    return pa.concat_batches(table.to_batches())


def _report(name: str, events: list[MutationEvent]) -> ChaosReport:
    return ChaosReport(
        name,
        sum(e.rows for e in events),
        {"events": [{"kind": e.kind, "column": e.column, "rows": e.rows} for e in events]},
    )


class _TableChaos:
    name = ""

    def _mutator(self) -> Mutator:
        raise NotImplementedError

    def _run(self, table: pa.Table, rng: np.random.Generator) -> tuple[Any, list[MutationEvent]]:
        return self._mutator().apply(table, _DAY, rng, _INTENSITY)

    def mutate(self, batch: pa.RecordBatch, seed: int) -> tuple[pa.RecordBatch, ChaosReport]:
        table = pa.Table.from_batches([batch])
        out, events = self._run(table, np.random.default_rng(seed))
        return _to_batch(out), _report(self.name, events)


class SchemaChaos(_TableChaos):
    name = "schema"

    def _mutator(self) -> Mutator:
        return SchemaChaosMutator()


class ValueChaos(_TableChaos):
    name = "value"

    def _mutator(self) -> Mutator:
        return ValueChaosMutator()


class TemporalChaos(_TableChaos):
    name = "temporal"

    def _mutator(self) -> Mutator:
        return TemporalChaosMutator()


class VolumeChaos(_TableChaos):
    name = "volume"

    def _mutator(self) -> Mutator:
        return VolumeChaosMutator()


class ReferentialChaos(_TableChaos):
    name = "referential"

    def _mutator(self) -> Mutator:
        return ReferentialChaosMutator()

    def _run(self, table: pa.Table, rng: np.random.Generator) -> tuple[Any, list[MutationEvent]]:
        tables, events = self._mutator().apply({"table": table}, _DAY, rng, _INTENSITY)
        return tables["table"], events


class FileChaos:
    name = "file"

    def mutate(self, batch: pa.RecordBatch, seed: int) -> tuple[pa.RecordBatch, ChaosReport]:
        sink = io.BytesIO()
        pacsv.write_csv(batch, sink)
        out, events = FileChaosMutator().apply(
            sink.getvalue(), _DAY, np.random.default_rng(seed), _INTENSITY
        )
        payload = pa.RecordBatch.from_arrays([pa.array([out], type=pa.binary())], names=["payload"])
        return payload, _report(self.name, events)
