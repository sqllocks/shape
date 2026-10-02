"""Hybrid simulator: a file drop and a stream emission of the same tables, linked (P6-04a).

The tables are split into *batch tables* (the file drop) and *stream tables* (the stream); by
default every table goes to both. With the ``correlation_id`` link strategy, one run id is

* written into every row of both sides as the ``_correlation_id`` column, and
* put in every manifest of the drop (``correlation_id``) and every event of the stream
  (``correlationid``),

so a consumer can join what arrived as files with what arrived as events. With ``natural_keys``
nothing is added and the two sides are joined on the tables' own keys.

The two phases run one after the other, or at the same time in two threads (``concurrent``). Each
keeps the seed of its own configuration.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.streaming.emit import EventSink
from shape_simulation import _tables as tb
from shape_simulation.file_drop import FileDropConfig, FileDropResult, FileDropSimulator
from shape_simulation.stream_emit import StreamEmitConfig, StreamEmitResult, StreamEmitter

CORRELATION_COLUMN = "_correlation_id"
LINK_STRATEGIES = ("correlation_id", "natural_keys")


@dataclass
class HybridConfig:
    """Configuration for :class:`HybridSimulator`.

    Args:
        stream_to: Where the stream is meant to land: ``"eventhouse"``, ``"lakehouse"`` or
            ``"both"``. Informational: the sink comes from ``stream_config``.
        micro_batch_to: Where the batches land (``"lakehouse_files"``). Informational.
        stream_tables: Tables for the stream; empty means all.
        batch_tables: Tables for the file drop; empty means all.
        stream_config: The stream side.
        file_drop_config: The batch side.
        link_strategy: ``"correlation_id"`` (default) or ``"natural_keys"``.
        concurrent: Run the two phases in parallel threads.
        seed: Informational: each side keeps its own configuration's seed.
    """

    stream_to: str = "eventhouse"
    micro_batch_to: str = "lakehouse_files"
    stream_tables: list[str] = field(default_factory=list)
    batch_tables: list[str] = field(default_factory=list)
    stream_config: StreamEmitConfig = field(default_factory=StreamEmitConfig)
    file_drop_config: FileDropConfig = field(default_factory=FileDropConfig)
    link_strategy: str = "correlation_id"
    concurrent: bool = False
    seed: int = 42

    def __post_init__(self) -> None:
        if self.link_strategy not in LINK_STRATEGIES:
            raise ValueError(
                f"unknown link_strategy {self.link_strategy!r}; use one of "
                f"{', '.join(LINK_STRATEGIES)}"
            )


@dataclass
class HybridResult:
    """Result of :meth:`HybridSimulator.run`.

    Attributes:
        file_drop_result: The batch phase (``None`` when no table went to it).
        stream_result: The stream phase (``None`` when no table went to it).
        correlation_id: The run id linking both outputs.
        link_strategy: The strategy that was applied.
    """

    file_drop_result: FileDropResult | None
    stream_result: StreamEmitResult | None
    correlation_id: str
    link_strategy: str

    def __repr__(self) -> str:
        batch, stream = self.file_drop_result, self.stream_result
        return (
            f"HybridResult(batch_files={len(batch.files_written) if batch else 0}, "
            f"stream_events={stream.total_events if stream else 0}, "
            f"link={self.link_strategy}, corr_id={self.correlation_id[:8]}...)"
        )


class HybridSimulator:
    """Run a file drop and a stream emission together, linked by one run id.

    Args:
        tables: ``{table name: table}`` (Arrow tables, pandas frames or a generation result).
        config: The two sides and how they are linked.
        sink: A sink for the stream side (see :class:`~shape_simulation.stream_emit.StreamEmitter`).

    Example::

        cfg = HybridConfig(
            file_drop_config=FileDropConfig(domain="retail", date_range_start="2024-01-01",
                                            date_range_end="2024-01-07"),
            stream_config=StreamEmitConfig(rate_per_sec=20),
            concurrent=True,
        )
        result = HybridSimulator(tables=generated.tables, config=cfg).run()
    """

    def __init__(
        self,
        tables: Any,
        config: HybridConfig | None = None,
        sink: EventSink | None = None,
    ) -> None:
        self._tables = tb.as_tables(tables)
        self._config = config or HybridConfig()
        self._sink = sink
        self._correlation_id = str(uuid.uuid4())

    @property
    def correlation_id(self) -> str:
        return self._correlation_id

    def run(self) -> HybridResult:
        cfg = self._config
        batch_tables = self._select_tables(cfg.batch_tables)
        stream_tables = self._select_tables(cfg.stream_tables)
        link = cfg.link_strategy == "correlation_id"
        if link:
            batch_tables = self._stamp_correlation(batch_tables)
            stream_tables = self._stamp_correlation(stream_tables)

        if cfg.concurrent:
            with ThreadPoolExecutor(max_workers=2) as pool:
                batch_future = pool.submit(self._run_file_drop, batch_tables, link)
                stream_future = pool.submit(self._run_stream_emit, stream_tables, link)
                drop, stream = batch_future.result(), stream_future.result()
        else:
            drop = self._run_file_drop(batch_tables, link)
            stream = self._run_stream_emit(stream_tables, link)
        return HybridResult(drop, stream, self._correlation_id, cfg.link_strategy)

    # ---- internals ----------------------------------------------------------------------

    def _select_tables(self, names: list[str]) -> dict[str, pa.Table]:
        """The tables called ``names`` (all of them when empty); an unknown name is an error."""
        if not names:
            return dict(self._tables)
        unknown = [n for n in names if n not in self._tables]
        if unknown:
            raise ValueError(
                f"unknown table {unknown[0]!r}; the tables are {', '.join(self._tables)}"
            )
        return {n: self._tables[n] for n in names}

    def _stamp_correlation(self, tables: dict[str, pa.Table]) -> dict[str, pa.Table]:
        """Every table with a ``_correlation_id`` column holding the run id."""
        out: dict[str, pa.Table] = {}
        for name, table in tables.items():
            stamp = pa.array([self._correlation_id] * table.num_rows, pa.string())
            if CORRELATION_COLUMN in table.column_names:
                index = table.schema.get_field_index(CORRELATION_COLUMN)
                out[name] = table.set_column(index, CORRELATION_COLUMN, stamp)
            else:
                out[name] = table.append_column(CORRELATION_COLUMN, stamp)
        return out

    def _run_file_drop(self, tables: dict[str, pa.Table], link: bool) -> FileDropResult | None:
        if not tables:
            return None
        sim = FileDropSimulator(
            tables,
            self._config.file_drop_config,
            correlation_id=self._correlation_id if link else None,
        )
        return sim.run()

    def _run_stream_emit(self, tables: dict[str, pa.Table], link: bool) -> StreamEmitResult | None:
        if not tables:
            return None
        emitter = StreamEmitter(
            tables,
            self._config.stream_config,
            self._sink,
            correlation_id=self._correlation_id if link else None,
        )
        return emitter.emit()
