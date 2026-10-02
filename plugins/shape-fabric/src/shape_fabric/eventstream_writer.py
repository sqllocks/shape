"""Eventstream batch writer: a table's rows as events into a Fabric Eventstream.

    writer = EventstreamWriter("eventstream://my-eventstream", connection_string=cs)
    writer.write_table("customer", batches)

It is the Eventstream emitter's transport (the Event Hubs protocol; :mod:`shape_fabric.
eventstream`) fed with a table instead of a run: each row becomes the same flat event the
emitter sends, with the key columns ``_shape_table`` and ``_shape_seq`` (the row's position in
its table), so a consumer can drop repeats. Delivery is at-least-once; the call returns after the
service has acknowledged every event. Write modes do not apply to a stream.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError
from shape.streaming.emit.formats import with_event_fields

from .errors import WriteError, WriteResult
from .eventstream import EventstreamEmitter


class EventstreamWriter:
    """Tables to one Eventstream custom endpoint."""

    def __init__(
        self,
        uri: str,
        *,
        connection_string: str | None = None,
        envelope: str = "flat",
        partition_key: str = "table",
        busy_retries: int = 6,
        client_factory: Callable[[Any, dict[str, Any]], Any] | None = None,
        busy_pause: float = 0.5,
    ) -> None:
        self.uri = uri
        self._emitter = EventstreamEmitter(client_factory, busy_pause=busy_pause)
        self._options: dict[str, Any] = {
            "envelope": envelope,
            "partition_key": partition_key,
            "busy_retries": busy_retries,
        }
        if connection_string:
            self._options["connection_string"] = connection_string

    @property
    def destination(self) -> str:
        return self.uri

    def write_table(self, table: str, batches: Iterable[pa.RecordBatch], **_ignored: Any) -> int:
        """Send ``table``'s rows; return the events acknowledged."""

        def events() -> Iterator[pa.RecordBatch]:
            seq = 0
            for batch in batches:
                if batch.num_rows:
                    yield with_event_fields(batch, table, seq)
                    seq += batch.num_rows

        try:
            return self._emitter.emit(self.uri, events(), **self._options)
        except ShapeError:
            raise
        except Exception as exc:
            raise WriteError(f"sending {table!r} to {self.uri} failed: {exc}") from exc

    def write_tables(self, tables: Mapping[str, Iterable[pa.RecordBatch]], **options: Any) -> WriteResult:
        start = time.monotonic()
        result = WriteResult(self.destination)
        for table, batches in tables.items():
            try:
                result.per_table[table] = self.write_table(table, batches, **options)
            except WriteError as exc:
                exc.result = result
                result.elapsed_seconds = time.monotonic() - start
                raise
        result.elapsed_seconds = time.monotonic() - start
        return result

    def close(self) -> None:
        self._emitter.close()
