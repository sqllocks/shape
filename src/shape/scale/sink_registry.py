"""``SinkRegistry``: one stream of batches to several sinks at once (P6-13).

Every sink gets every batch. The sinks of one call run in parallel on a pool that lives from
``open`` to ``close`` (a pool per batch would start and stop its threads for every chunk). A call
that fails in any sink raises :class:`~shape.scale.sinks.base.SinkError` after the other sinks have
finished the same call, and ``close`` always closes every sink, even after a failure.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from shape.scale.sinks.base import Sink, SinkError, sink_name
from shape.security.redact import redact_text

if TYPE_CHECKING:
    import pyarrow as pa  # type: ignore[import-untyped]

    from shape.generation.schema import GenSchema

logger = logging.getLogger(__name__)


class SinkRegistry:
    """Fan-out coordinator for a list of sinks."""

    def __init__(self, sinks: Sequence[Sink], max_workers: int = 8) -> None:
        self._sinks = list(sinks)
        self._max_workers = max(1, max_workers)
        self._pool: ThreadPoolExecutor | None = None
        self._opened: list[Sink] = []

    @property
    def sinks(self) -> list[Sink]:
        return list(self._sinks)

    def _each(self, label: str, call: Callable[[Sink], Any], targets: Sequence[Sink]) -> None:
        if not targets:
            return
        errors: list[tuple[str, Exception]] = []
        if len(targets) == 1 or self._pool is None:
            for sink in targets:
                try:
                    call(sink)
                except Exception as exc:
                    errors.append((sink_name(sink), exc))
        else:
            futures = [(sink, self._pool.submit(call, sink)) for sink in targets]
            for sink, future in futures:
                try:
                    future.result()
                except Exception as exc:
                    errors.append((sink_name(sink), exc))
        if errors:
            for name, err in errors:
                logger.error("sink %s: %s failed: %s", name, label, redact_text(str(err)))
            raise SinkError(errors)

    def open(self, schema: GenSchema | None) -> None:
        if len(self._sinks) > 1:
            self._pool = ThreadPoolExecutor(
                max_workers=min(self._max_workers, len(self._sinks)),
                thread_name_prefix="shape-sink",
            )
        opened: list[Sink] = []

        def open_one(sink: Sink) -> None:
            sink.open(schema)
            opened.append(sink)

        try:
            self._each("open", open_one, self._sinks)
        finally:
            # a sink whose open failed is not closed; the ones that opened are, by ``close``
            self._opened = [s for s in self._sinks if s in opened]

    def write_batch(self, table: str, batch: pa.RecordBatch) -> None:
        self._each("write_batch", lambda s: s.write_batch(table, batch), self._sinks)

    def finish_table(self, table: str) -> None:
        def finish(sink: Sink) -> None:
            hook = getattr(sink, "finish_table", None)
            if hook is not None:
                hook(table)

        self._each("finish_table", finish, self._sinks)

    def close(self) -> None:
        """Close every sink that was opened; raise one ``SinkError`` for all that failed."""
        errors: list[tuple[str, Exception]] = []
        for sink in self._opened:
            try:
                sink.close()
            except Exception as exc:
                errors.append((sink_name(sink), exc))
                logger.error("sink %s: close failed: %s", sink_name(sink), redact_text(str(exc)))
        self._opened = []
        pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown(wait=True)
        if errors:
            raise SinkError(errors)
