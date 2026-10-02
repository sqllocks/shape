"""``MemorySink``: every batch kept in memory, one Arrow table per generated table."""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING, Any

from shape.scale.sinks.base import BaseSink

if TYPE_CHECKING:
    import pyarrow as pa  # type: ignore[import-untyped]

    from shape.generation.schema import GenSchema

_WARN_BYTES = 4 * 1024**3


class MemorySink(BaseSink):
    """Accumulates the batches of every table; ``result()`` returns them as Arrow tables.

    ``max_memory_gb`` makes a run that would hold more raise ``MemoryError`` instead of filling the
    machine. Past 4 GB the sink warns once: Parquet or a Fabric sink is the sink for that size.
    """

    name = "memory"

    def __init__(self, max_memory_gb: float | None = None) -> None:
        self._max_bytes = int(max_memory_gb * 1024**3) if max_memory_gb is not None else None
        self._batches: dict[str, list[pa.RecordBatch]] = {}
        self._bytes = 0
        self._warned = False

    def open(self, schema: GenSchema | None) -> None:
        self._batches = {}
        self._bytes = 0
        self._warned = False

    def write_batch(self, table: str, batch: pa.RecordBatch) -> None:
        new_total = self._bytes + batch.nbytes
        if self._max_bytes is not None and new_total > self._max_bytes:
            raise MemoryError(
                f"MemorySink exceeded max_memory_gb ({new_total / 1024**3:.1f} GB held)"
            )
        if new_total > _WARN_BYTES and not self._warned:
            self._warned = True
            warnings.warn(
                f"MemorySink holds {new_total / 1024**3:.1f} GB; use a Parquet or Fabric sink "
                "for a workload this size",
                ResourceWarning,
                stacklevel=2,
            )
        self._batches.setdefault(table, []).append(batch)
        self._bytes = new_total

    def result(self) -> dict[str, Any]:
        """One ``pyarrow.Table`` per table, in the order the tables were first written."""
        import pyarrow as pa

        return {
            name: pa.Table.from_batches(batches, schema=batches[0].schema)
            for name, batches in self._batches.items()
            if batches
        }

    @property
    def estimated_bytes(self) -> int:
        return self._bytes
