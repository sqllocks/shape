"""Delta sink (``[delta]`` extra, deltalake): ``<uri>/<table>`` as a Delta table.

``uri`` is the directory that holds the tables. Options: ``mode`` (``overwrite`` by default, or
``append``) and ``partition_by`` (a list of column names).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.builtins.sources.files import local_path
from shape.plugins.schemes import require_scheme


class DeltaSink:
    name = "delta"
    schemes = ("file",)
    extension = ""  # no file extension: a directory per table

    def write(self, uri: str, table: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        require_scheme(self, uri)
        try:
            from deltalake import write_deltalake
        except ImportError as exc:
            raise ImportError(
                "writing Delta needs deltalake: pip install 'sqllocks-shape[delta]'"
            ) from exc
        target = local_path(uri) / table
        target.mkdir(parents=True, exist_ok=True)
        stream = iter(batches)
        first = next(stream, None)
        schema = first.schema if first is not None else options.get("schema")
        if schema is None:
            raise ValueError(
                "a Delta table needs a schema: pass `schema` when there are no batches"
            )
        rows = 0

        def counted() -> Iterator[pa.RecordBatch]:
            nonlocal rows
            if first is not None:
                rows += first.num_rows
                yield first
            for batch in stream:
                rows += batch.num_rows
                yield batch

        write_deltalake(
            str(target),
            pa.RecordBatchReader.from_batches(schema, counted()),
            mode=options.get("mode", "overwrite"),
            partition_by=options.get("partition_by") or None,
        )
        return rows
