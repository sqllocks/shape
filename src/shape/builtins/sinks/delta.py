"""Delta sink (``[delta]`` extra, deltalake): ``<uri>/<table>`` as a Delta table.

``uri`` is the directory that holds the tables: a local path, or ``delta+abfss://<container>@
<host>/<folder>`` for OneLake (``.../<lakehouse>.Lakehouse/Tables``) and ADLS Gen2, with the
credential options of the ``abfss`` source (:mod:`shape.builtins.sources._azure_auth`; a bearer
token, an account key or a SAS token; delta-rs takes no connection string).

Options: ``mode`` (``overwrite`` by default, or ``append``), ``partition_by`` (column names),
``commit_rows`` / ``commit_seconds`` (micro-batch mode: a Delta commit every N rows or seconds
instead of one at the end, so Fabric and Spark readers see rows while a stream runs; the first
commit applies ``mode``, later ones append), ``storage_options``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Iterator
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.builtins.sources.files import local_path
from shape.plugins.schemes import require_scheme

CLOUD_PREFIX = "delta+"


def _deltalake() -> Any:
    try:
        import deltalake
    except ImportError as exc:
        raise ImportError(
            "writing Delta needs deltalake: pip install 'sqllocks-shape[delta]'"
        ) from exc
    return deltalake


def _location(uri: str, table: str, options: dict[str, Any]) -> tuple[str, dict[str, str] | None]:
    """The table's location and delta-rs storage options."""
    if uri.startswith(CLOUD_PREFIX):
        from shape.builtins.sources.azure import parse
        from shape.builtins.sources.delta import _storage_options
        from shape.security.names import safe_name

        base = uri[len(CLOUD_PREFIX) :]
        parse(base)  # validates the URI
        return f"{base.rstrip('/')}/{safe_name(table)}", _storage_options(base, options)
    from shape.security.names import contained

    target = contained(local_path(uri), table)
    target.mkdir(parents=True, exist_ok=True)
    extra = {str(k): str(v) for k, v in dict(options.get("storage_options") or {}).items()}
    return str(target), extra or None


class DeltaTableWriter:
    """Batches in, Delta commits out: ``flush`` and the thresholds each make one commit."""

    def __init__(
        self,
        location: str,
        schema: pa.Schema,
        options: dict[str, Any],
        storage: dict[str, str] | None,
    ) -> None:
        self._write = _deltalake().write_deltalake
        self._location = location
        self._schema = schema
        self._storage = storage
        self._partition_by = options.get("partition_by") or None
        self._mode = options.get("mode", "overwrite")
        self.commit_rows = int(options["commit_rows"]) if options.get("commit_rows") else None
        self.commit_seconds = (
            float(options["commit_seconds"]) if options.get("commit_seconds") else None
        )
        self._clock: Callable[[], float] = options.get("clock") or time.monotonic
        self._pending: list[pa.RecordBatch] = []
        self._pending_rows = 0
        self._since = self._clock()
        self.rows = 0
        self.commits = 0

    def write_batch(self, batch: pa.RecordBatch) -> None:
        if batch.num_rows:
            self._pending.append(batch)
            self._pending_rows += batch.num_rows
        if self.commit_rows is None and self.commit_seconds is None:
            return
        if (self.commit_rows is not None and self._pending_rows >= self.commit_rows) or (
            self.commit_seconds is not None and self._clock() - self._since >= self.commit_seconds
        ):
            self.flush()

    def write_all(self, batches: Iterable[pa.RecordBatch]) -> None:
        for batch in batches:
            self.write_batch(batch)

    def flush(self) -> None:
        """Commit what is pending (nothing when it is empty)."""
        self._since = self._clock()
        if not self._pending:
            return
        self._commit(self._pending)

    def _commit(self, batches: list[pa.RecordBatch]) -> None:
        reader = pa.RecordBatchReader.from_batches(self._schema, iter(batches))
        self._write(
            self._location,
            reader,
            mode=self._mode,
            partition_by=self._partition_by,
            storage_options=self._storage,
        )
        self.rows += self._pending_rows
        self.commits += 1
        self._pending, self._pending_rows = [], 0
        self._mode = "append"  # only the first commit replaces

    def close(self, *, schema: pa.Schema | None = None) -> int:
        if self._pending or self.commits == 0:
            self._commit(self._pending)
        return self.rows

    def abort(self) -> None:
        self._pending, self._pending_rows = [], 0


class DeltaSink:
    name = "delta"
    schemes = ("file", "delta+abfss", "delta+abfs")
    extension = ""  # no file extension: a directory per table

    def open_table(
        self, uri: str, table: str, schema: pa.Schema | None = None, **options: Any
    ) -> DeltaTableWriter:
        """A streaming writer for ``table``; the schema comes from ``schema``."""
        require_scheme(self, uri)
        if schema is None:
            schema = options.get("schema")
        if schema is None:
            raise ValueError("a Delta table needs a schema: pass `schema`")
        location, storage = _location(uri, table, options)
        return DeltaTableWriter(location, schema, options, storage)

    def write(self, uri: str, table: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        require_scheme(self, uri)
        stream = iter(batches)
        first = next(stream, None)
        schema = first.schema if first is not None else options.get("schema")
        if schema is None:
            raise ValueError(
                "a Delta table needs a schema: pass `schema` when there are no batches"
            )
        if options.get("commit_rows") or options.get("commit_seconds"):
            writer = self.open_table(uri, table, schema, **options)

            def chained() -> Iterator[pa.RecordBatch]:
                if first is not None:
                    yield first
                yield from stream

            try:
                writer.write_all(chained())
            except BaseException:
                writer.abort()
                raise
            return writer.close()
        location, storage = _location(uri, table, options)
        write_deltalake = _deltalake().write_deltalake
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
            location,
            pa.RecordBatchReader.from_batches(schema, counted()),
            mode=options.get("mode", "overwrite"),
            partition_by=options.get("partition_by") or None,
            storage_options=storage,
        )
        return rows
