"""``MultiStoreWriter``: write finished tables to several writers at once (P6-13).

Where the scale router fans a *stream* of chunks out to sinks, this fans a *finished* set of tables
out to writers: anything with ``write_all(tables, **kwargs)`` (the Lakehouse, Warehouse, SQL
Database and Eventhouse writers, a file writer, or a scale sink through :func:`sink_writer`). The
writers run in parallel threads. A writer that fails does not stop the others; its error is in the
result, and with ``raise_on_error`` one ``MultiStoreError`` names every failure after all have
finished.

Results are keyed by a label that is unique per writer (its ``name``, else its class name, with
``#2``, ``#3`` for repeats), so two writers of one kind never overwrite each other's result.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    import pyarrow as pa  # type: ignore[import-untyped]


@runtime_checkable
class TableWriter(Protocol):
    """Any writer of a set of finished tables."""

    def write_all(self, tables: Mapping[str, pa.Table], **kwargs: Any) -> Any: ...


class MultiStoreError(RuntimeError):
    """One or more writers failed; ``errors`` maps each label to its exception."""

    def __init__(self, errors: Mapping[str, BaseException]) -> None:
        self.errors = dict(errors)
        super().__init__(
            "writer failures: " + "; ".join(f"{k}: {v}" for k, v in self.errors.items())
        )


@dataclass
class MultiStoreResult:
    """What each writer returned, and what each failed with."""

    results: dict[str, Any] = field(default_factory=dict)
    errors: dict[str, BaseException] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return not self.errors

    def __repr__(self) -> str:
        return (
            f"MultiStoreResult(writers={len(self.results) + len(self.errors)}, "
            f"ok={len(self.results)}, errors={len(self.errors)})"
        )


def _labels(writers: Sequence[Any]) -> list[str]:
    seen: dict[str, int] = {}
    labels = []
    for writer in writers:
        base = getattr(writer, "name", None)
        base = base if isinstance(base, str) and base else type(writer).__name__
        seen[base] = seen.get(base, 0) + 1
        labels.append(base if seen[base] == 1 else f"{base}#{seen[base]}")
    return labels


class MultiStoreWriter:
    """Fans ``write_all`` out to every writer in parallel."""

    def __init__(
        self,
        writers: Sequence[TableWriter],
        max_workers: int | None = None,
        raise_on_error: bool = False,
    ) -> None:
        if not writers:
            raise ValueError("MultiStoreWriter needs at least one writer")
        for writer in writers:
            if not hasattr(writer, "write_all"):
                raise TypeError(f"{type(writer).__name__} has no write_all(tables)")
        self._writers = list(writers)
        self._labels = _labels(self._writers)
        self._max_workers = max(1, max_workers or len(writers))
        self._raise = raise_on_error

    def write_all(self, tables: Mapping[str, pa.Table], **kwargs: Any) -> MultiStoreResult:
        result = MultiStoreResult()

        def call(writer: TableWriter) -> Any:
            return writer.write_all(tables, **kwargs)

        with ThreadPoolExecutor(max_workers=self._max_workers, thread_name_prefix="shape-store") as pool:
            futures = [(label, pool.submit(call, w)) for label, w in zip(self._labels, self._writers, strict=True)]
            for label, future in futures:
                try:
                    out = future.result()
                except Exception as exc:
                    result.errors[label] = exc
                    continue
                # A writer result with ``success`` false (and optional ``errors``) is a failure.
                if getattr(out, "success", True) is False:
                    detail = getattr(out, "errors", None) or ["unspecified"]
                    result.errors[label] = RuntimeError(
                        f"{label} reported {len(detail)} error(s): {'; '.join(map(str, detail))}"
                    )
                else:
                    result.results[label] = out
        if self._raise and result.errors:
            raise MultiStoreError(result.errors)
        return result

    def __repr__(self) -> str:
        return f"MultiStoreWriter([{', '.join(self._labels)}])"


class _SinkWriter:
    """A scale sink as a ``write_all`` writer."""

    def __init__(self, sink: Any, schema: Any = None) -> None:
        self.name = str(getattr(sink, "name", type(sink).__name__))
        self._sink = sink
        self._schema = schema

    def write_all(self, tables: Mapping[str, pa.Table], **kwargs: Any) -> dict[str, int]:
        self._sink.open(self._schema)
        try:
            for name, table in tables.items():
                batches = table.to_batches() or [_empty(table)]
                for batch in batches:
                    self._sink.write_batch(name, batch)
                finish = getattr(self._sink, "finish_table", None)
                if finish is not None:
                    finish(name)
        finally:
            self._sink.close()
        return {name: int(table.num_rows) for name, table in tables.items()}


def _empty(table: pa.Table) -> Any:
    import pyarrow as pa

    return pa.RecordBatch.from_arrays(
        [pa.array([], type=f.type) for f in table.schema], schema=table.schema
    )


def sink_writer(sink: Any, schema: Any = None) -> TableWriter:
    """``sink`` (a scale sink) as a writer ``MultiStoreWriter`` can drive."""
    return _SinkWriter(sink, schema)


__all__ = [
    "MultiStoreError",
    "MultiStoreResult",
    "MultiStoreWriter",
    "TableWriter",
    "sink_writer",
]
