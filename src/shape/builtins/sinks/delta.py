"""Delta sink (``[delta]`` extra, deltalake): ``<uri>/<table>`` as a Delta table.

``uri`` is the directory that holds the tables. Options: ``mode`` (``overwrite`` by default, or
``append``) and ``partition_by`` (a list of column names).

Tables are written at Delta reader version 1 / writer version 2, with no table features and no
table configuration, so that every engine can read them (Python notebooks, pipelines, Spark,
the SQL endpoint). A timestamp without a time zone would make the writer ask for the
``timestampNtz`` feature (reader 3 / writer 7), so such columns are written as UTC timestamps
(the wall-clock value is kept, read as UTC).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.builtins.sources.files import local_path


def _utc_timestamps(schema: pa.Schema) -> pa.Schema:
    """``schema`` with every time-zone-less timestamp (at any nesting depth) made a UTC one."""
    return pa.schema([f.with_type(_utc_type(f.type)) for f in schema], metadata=schema.metadata)


def _utc_type(t: pa.DataType) -> pa.DataType:
    if pa.types.is_timestamp(t) and t.tz is None:
        return pa.timestamp(t.unit, tz="UTC")
    if pa.types.is_list(t) or pa.types.is_large_list(t):
        return type(t)(t.value_field.with_type(_utc_type(t.value_type)))
    if pa.types.is_struct(t):
        return pa.struct(
            [t.field(i).with_type(_utc_type(t.field(i).type)) for i in range(t.num_fields)]
        )
    return t


class DeltaSink:
    name = "delta"
    schemes = ("file",)

    def write(self, uri: str, table: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        try:
            from deltalake import write_deltalake
        except ImportError as exc:
            raise ImportError(
                "writing Delta needs deltalake: pip install 'sqllocks-shape[delta]'"
            ) from exc
        from shape.security.names import contained

        target = contained(local_path(uri), table)
        target.mkdir(parents=True, exist_ok=True)
        stream = iter(batches)
        first = next(stream, None)
        schema = first.schema if first is not None else options.get("schema")
        if schema is None:
            raise ValueError(
                "a Delta table needs a schema: pass `schema` when there are no batches"
            )
        schema = _utc_timestamps(schema)
        rows = 0

        def counted() -> Iterator[pa.RecordBatch]:
            nonlocal rows
            if first is not None:
                rows += first.num_rows
                yield first.cast(schema)
            for batch in stream:
                rows += batch.num_rows
                yield batch.cast(schema)

        write_deltalake(
            str(target),
            pa.RecordBatchReader.from_batches(schema, counted()),
            mode=options.get("mode", "overwrite"),
            partition_by=options.get("partition_by") or None,
        )
        return rows
