"""Distributed profiling for Spark (Fabric PySpark notebooks, Synapse Spark).

``profile_distributed(df)`` profiles a Spark ``DataFrame`` without moving its rows to the driver:

1. every partition streams its Arrow batches (``DataFrame.mapInArrow``) into a bounded-mode
   kernel ``ProfileState`` (T-15: sketches, memory independent of the row count) and returns its
   ``snapshot()`` as a single binary cell;
2. the driver receives the snapshots one partition at a time (``toLocalIterator``, so no
   ``spark.driver.maxResultSize`` pile-up), restores each with ``from_snapshot`` and folds it into
   one state with ``merge`` (the T-14 merges: HLL union, KLL compaction, SpaceSaving with error
   terms);
3. the merged state is finalized into the same ``profile-engine-v1`` document that
   ``shape.profile.engine.profile(..., mode="bounded")`` returns for one process.

Partitions are merged in partition order, so the result is deterministic for a given
partitioning. First-appearance ordering follows that order, which is not the source row order
once Spark has shuffled the data. The bounded statistics (counts, min/max, mean, variance,
null and distinct counts, quantiles, top values) agree with the single-process bounded profile
within the sketches' error bounds (``error_models`` records them). Shape must be installed on
the executors too (a Fabric Environment library, or the pool or workspace packages of a
Synapse Spark pool).

Spark is imported lazily; this module imports without ``pyspark``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.profile.engine import _document, table_entry
from shape.profile.nested import profile_state, restore_state

__all__ = ["SNAPSHOT_SPARK_SCHEMA", "profile_distributed"]

SNAPSHOT_SPARK_SCHEMA = "partition long, rows long, arrow_schema binary, snapshot binary"
_SNAPSHOT_ARROW_SCHEMA = pa.schema(
    [
        ("partition", pa.int64()),
        ("rows", pa.int64()),
        ("arrow_schema", pa.binary()),
        ("snapshot", pa.binary()),
    ]
)


def _partition_snapshot(batches: Iterator[pa.RecordBatch]) -> Iterator[pa.RecordBatch]:
    """Runs on an executor: fold one partition into a bounded-mode state, emit its snapshot.

    An empty partition emits nothing. Peak memory is one Arrow batch plus the state, which does
    not grow with the partition's row count.
    """
    from pyspark import TaskContext

    state: Any = None
    schema: pa.Schema | None = None
    rows = 0
    for batch in batches:
        if state is None:
            schema = batch.schema
            state = profile_state(schema, "bounded")
        state.update(batch)
        rows += batch.num_rows
    if state is None or schema is None:
        return
    context = TaskContext.get()
    partition = context.partitionId() if context is not None else 0
    yield pa.record_batch(
        [
            pa.array([partition], pa.int64()),
            pa.array([rows], pa.int64()),
            pa.array([schema.serialize().to_pybytes()], pa.binary()),
            pa.array([state.snapshot()], pa.binary()),
        ],
        schema=_SNAPSHOT_ARROW_SCHEMA,
    )


def _driver_schema(df: Any) -> pa.Schema:
    """The Arrow schema of ``df`` as Spark sends it, for a DataFrame with no rows."""
    from pyspark.sql.pandas.types import to_arrow_schema

    return pa.schema(to_arrow_schema(df.schema))


def profile_distributed(
    df: Any, *, name: str | None = None, top_n: int = 500, partitions: int | None = None
) -> dict[str, Any]:
    """Profile a Spark DataFrame in bounded mode, one partial profile per partition.

    ``partitions`` repartitions first (more, smaller tasks); by default the DataFrame's own
    partitioning is used. Returns the table document (``{"schema_version", "engine", "mode":
    "bounded", "tables": {name: {name, rows, columns}}}``).
    """
    if top_n < 1:
        raise ValueError("top_n must be positive")
    if partitions is not None:
        if partitions < 1:
            raise ValueError("partitions must be positive")
        df = df.repartition(partitions)
    table_name = name or "table"
    snapshots = df.mapInArrow(_partition_snapshot, SNAPSHOT_SPARK_SCHEMA)

    schema: pa.Schema | None = None
    merged: Any = None
    for row in snapshots.toLocalIterator():  # partition order; one partition's cell at a time
        part_schema = pa.ipc.read_schema(pa.py_buffer(bytes(row["arrow_schema"])))
        if schema is None:
            schema = part_schema
        elif not part_schema.equals(schema):
            raise ValueError(
                f"partition {row['partition']} has schema {part_schema}, expected {schema}"
            )
        state = restore_state(schema, bytes(row["snapshot"]))
        if merged is None:
            merged = state
        else:
            merged.merge(state)
    if merged is None:  # no partition produced a batch: an empty table
        schema = _driver_schema(df)
        merged = profile_state(schema, "bounded")
    assert schema is not None
    entry = table_entry(merged, table_name, schema, "bounded", top_n)
    return _document("bounded", {table_name: entry})
