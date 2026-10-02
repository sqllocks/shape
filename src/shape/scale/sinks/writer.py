"""``WriterSink``: a sink that streams each table through a ``shape.sinks`` writer.

A writer is anything with ``write(uri, table, batches, **options) -> rows`` (the ``shape.sinks``
plugin contract: the file formats, and the Fabric writers). The first batch of a table starts a
thread that calls ``writer.write`` with an iterator over a bounded queue, so a table flows into
the destination as it is generated and only a few batches are held (not every table until the
end). ``finish_table`` ends the queue and waits for the writer, so a failed load raises there,
table by table.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Iterator, Mapping
from typing import TYPE_CHECKING, Any

from shape.scale.sinks.base import BaseSink

if TYPE_CHECKING:
    import pyarrow as pa  # type: ignore[import-untyped]

    from shape.generation.schema import GenSchema

_END = object()
_DEPTH = 4


class _TableStream:
    """One table's writer thread and the queue that feeds it."""

    def __init__(self, write: Callable[[Iterator[Any]], int], table: str) -> None:
        self.table = table
        self.rows = 0
        self.error: BaseException | None = None
        self._ended = False  # the writer has read the end marker
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=_DEPTH)
        self._write = write
        self._thread = threading.Thread(target=self._run, name=f"shape-writer-{table}", daemon=True)
        self._thread.start()

    def _drain(self) -> Iterator[Any]:
        while (item := self._queue.get()) is not _END:
            yield item
        self._ended = True

    def _run(self) -> None:
        try:
            self._write(self._drain())
        except BaseException as exc:
            self.error = exc
            # Keep the producer from blocking: read what is left, up to the end marker (a writer
            # that fails after reading everything has read it already).
            while not self._ended and self._queue.get() is not _END:
                pass

    def put(self, batch: pa.RecordBatch) -> None:
        while True:
            if self.error is not None:
                raise self.error
            try:
                self._queue.put(batch, timeout=0.2)
                return
            except queue.Full:
                continue

    def finish(self) -> None:
        self._queue.put(_END)
        self._thread.join()
        if self.error is not None:
            raise self.error


class WriterSink(BaseSink):
    """Streams every table into ``writer.write(uri, table, batches, **options)``."""

    name = "writer"

    def __init__(
        self,
        writer: Any | Callable[[], Any],
        uri: str,
        options: Mapping[str, Any] | None = None,
        *,
        name: str = "writer",
    ) -> None:
        self._writer_source = writer
        self._writer: Any = None
        self._uri = uri
        self._options = dict(options or {})
        self.name = name
        self._streams: dict[str, _TableStream] = {}
        self.rows_written: dict[str, int] = {}

    @property
    def uri(self) -> str:
        return self._uri

    def _resolve(self) -> Any:
        source = self._writer_source
        return source() if callable(source) and not hasattr(source, "write") else source

    def open(self, schema: GenSchema | None) -> None:
        self._writer = self._resolve()
        self._streams = {}
        self.rows_written = {}

    def _stream(self, table: str, schema: pa.Schema) -> _TableStream:
        stream = self._streams.get(table)
        if stream is None:
            writer, uri, options = self._writer, self._uri, self._options

            def write(batches: Iterator[Any]) -> int:
                rows = int(writer.write(uri, table, batches, schema=schema, **options))
                self.rows_written[table] = rows
                return rows

            stream = self._streams[table] = _TableStream(write, table)
        return stream

    def write_batch(self, table: str, batch: pa.RecordBatch) -> None:
        self._stream(table, batch.schema).put(batch)

    def finish_table(self, table: str) -> None:
        stream = self._streams.pop(table, None)
        if stream is not None:
            stream.finish()

    def close(self) -> None:
        errors: list[BaseException] = []
        for table in list(self._streams):
            try:
                self.finish_table(table)
            except BaseException as exc:
                errors.append(exc)
        if errors:
            raise errors[0]
