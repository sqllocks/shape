"""Stream emitter: generated tables as enveloped streaming events (P6-04a).

Built on the emit runtime (``shape.streaming.emit``): the runtime paces the events (a rate, bursts),
delivers them to a sink with backpressure and retries, and the sinks are the runtime's own
(standard output, a JSON Lines file, any ``shape.emitters`` plugin by URI). What this module adds
is the *sequence*: the rows of the tables you give it, wrapped as CloudEvents-style events, with

* **an envelope**: ``specversion``, ``id`` (``<table>/<row position>``, so a replay repeats it),
  ``source``, ``type`` (``shape.<topic>``), ``time`` (when the event was sent),
  ``datacontenttype``, ``topic``, ``schemaversion``, ``correlationid`` (one id for the run),
  ``shapetable``, ``shapeseq`` and ``data`` (the row, with ``_shape_table``, ``_shape_seq`` and,
  when the table has a date or timestamp column, ``_shape_event_time``);
* **out-of-order delivery**: a share of the events swap places with their successor;
* **a replay window**: after an event, with some probability, the last few events are sent again
  (marked ``replay`` and ``replaytime``);
* **schema versions** per topic.

The random draws are made in a fixed order (the out-of-order swaps first; then per event the
jitter and the replay decision), so a seed reproduces a run.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]

from shape.streaming.emit import (
    AnomalyInjector,
    Burst,
    EmitConfig,
    EmitRunner,
    EventBlock,
    EventSink,
    FileSink,
    StdoutSink,
    open_sink,
)
from shape.streaming.emit.formats import (
    FIELD_SEQ,
    FIELD_TABLE,
    FIELD_TIME,
    check_reserved,
    event_columns,
    event_time_column,
    rows_of,
)
from shape_simulation import _tables as tb

BLOCK_EVENTS = 4096


@dataclass
class BurstWindow:
    """A time window in which the event rate is multiplied (realtime runs).

    Args:
        start_offset_seconds: Seconds after the start when the burst begins.
        duration_seconds: How long it lasts.
        multiplier: Rate multiplier during the burst (10.0 is ten times the base rate).
    """

    start_offset_seconds: float
    duration_seconds: float
    multiplier: float

    def to_burst(self) -> Burst:
        return Burst(self.start_offset_seconds, self.duration_seconds, self.multiplier)


@dataclass
class StreamEmitConfig:
    """Configuration for :class:`StreamEmitter`.

    Args:
        rate_per_sec: Events per second (realtime runs).
        jitter_ms: Largest random pause added before an event (realtime runs).
        burst_windows: Rate bursts (realtime runs; they may not overlap).
        out_of_order_probability: Share of the events delivered out of order.
        replay_enabled: Send the most recent events again from time to time.
        replay_window_minutes: Length of the replay window, at the base rate.
        replay_probability: Per-event probability of a replay.
        replay_burst_size: Events sent again by one replay.
        topics: Topic names, one per table in order, or one name for all of them; empty uses the
            table names.
        envelope_source: The envelope's ``source``.
        envelope_schema_version: Schema version of every topic.
        sink_type: ``"console"``, ``"file"`` or the URI of an emitter (``kafka://...``,
            ``eventhubs://...``, ``eventstream://...``).
        sink_connection: For a file: ``path`` (default ``events.jsonl``) and ``mode`` (``"w"`` or
            ``"a"``).
        max_events: Stop after this many events (replays not counted).
        realtime: Pace the events at ``rate_per_sec`` (otherwise as fast as the sink takes them).
        seed: Random seed.
    """

    rate_per_sec: float = 10.0
    jitter_ms: float = 0.0
    burst_windows: list[BurstWindow] = field(default_factory=list)
    out_of_order_probability: float = 0.0
    replay_enabled: bool = False
    replay_window_minutes: float = 5.0
    replay_probability: float = 0.05
    replay_burst_size: int = 10
    topics: list[str] = field(default_factory=list)
    envelope_source: str = "shape"
    envelope_schema_version: str = "1.0"
    sink_type: str = "console"
    sink_connection: dict[str, Any] = field(default_factory=dict)
    max_events: int | None = None
    realtime: bool = False
    seed: int = 42

    def __post_init__(self) -> None:
        if not 0.0 <= self.out_of_order_probability <= 1.0:
            raise ValueError("out_of_order_probability must be between 0 and 1")
        if not 0.0 <= self.replay_probability <= 1.0:
            raise ValueError("replay_probability must be between 0 and 1")
        if self.max_events is not None and self.max_events < 0:
            raise ValueError("max_events must be 0 or more")
        if self.jitter_ms < 0:
            raise ValueError("jitter_ms must be 0 or more")


@dataclass
class StreamEmitResult:
    """Result of :meth:`StreamEmitter.emit`.

    Attributes:
        events_sent: Primary events sent.
        replay_events_sent: Events sent again by the replay window.
        topics_used: The topics written to.
        elapsed_seconds: Wall-clock duration.
        schema_versions: Topic to schema version.
    """

    events_sent: int
    replay_events_sent: int
    topics_used: set[str]
    elapsed_seconds: float
    schema_versions: dict[str, str]

    @property
    def total_events(self) -> int:
        return self.events_sent + self.replay_events_sent

    def __repr__(self) -> str:
        return (
            f"StreamEmitResult(sent={self.events_sent}, replays={self.replay_events_sent}, "
            f"topics={self.topics_used}, {self.elapsed_seconds:.2f}s)"
        )


def topic_map(tables: dict[str, pa.Table], cfg: StreamEmitConfig) -> dict[str, str]:
    """Table name to topic: ``cfg.topics`` by position when there is one per table, one topic for
    every table when there is a single name, else the table names."""
    names = list(tables)
    if cfg.topics and len(cfg.topics) == len(names):
        return dict(zip(names, cfg.topics, strict=True))
    if cfg.topics and len(cfg.topics) == 1:
        return {t: cfg.topics[0] for t in names}
    return {t: t for t in names}


class TablesEventPlan:
    """The events of some tables as a counted, resumable sequence (what the emit runtime needs).

    Rows go out table by table in row order; out-of-order swaps, the ``max_events`` cut and the
    replays are decided here, up front, from one random stream, so ``blocks(offset)`` resumes
    anywhere and gives the same events.
    """

    anomaly: AnomalyInjector | None = None

    def __init__(
        self,
        tables: dict[str, pa.Table],
        cfg: StreamEmitConfig,
        topics: dict[str, str],
        rng: np.random.Generator,
        correlation_id: str,
        replay_buffer: int,
    ) -> None:
        self._tables = tables
        self._cfg = cfg
        self._topics = topics
        self._correlation_id = correlation_id
        self._names = list(tables)
        counts = [tables[t].num_rows for t in self._names]
        table_id = np.repeat(np.arange(len(self._names)), counts)
        row = np.concatenate([np.arange(c) for c in counts]) if counts else np.zeros(0, np.int64)

        if cfg.out_of_order_probability > 0 and len(row) > 1:
            self._swap(table_id, row, rng, cfg.out_of_order_probability)
        if cfg.max_events is not None:
            table_id, row = table_id[: cfg.max_events], row[: cfg.max_events]
        self._table_id = table_id.astype(np.int64)
        self._row = row.astype(np.int64)
        self.primary_events = len(row)

        self._jitter: np.ndarray[Any, Any] | None = None
        draws = (1 if cfg.jitter_ms > 0 else 0) + (1 if cfg.replay_enabled else 0)
        n = self.primary_events
        replay_draws = np.zeros(n)
        if draws:
            block = rng.random(n * draws).reshape(n, draws)
            if cfg.jitter_ms > 0:
                self._jitter = block[:, 0] * (cfg.jitter_ms / 1000.0)
            if cfg.replay_enabled:
                replay_draws = block[:, draws - 1]
        self._out_ref, self._out_replay = self._order(
            replay_draws < cfg.replay_probability if cfg.replay_enabled else None, replay_buffer
        )
        self.total_events = len(self._out_ref)
        self.replay_events = int(self._out_replay.sum())

    @staticmethod
    def _swap(
        table_id: np.ndarray[Any, Any],
        row: np.ndarray[Any, Any],
        rng: np.random.Generator,
        probability: float,
    ) -> None:
        """Swap a share of the events with their successor, in the order the rng picks them."""
        n = len(row)
        count = max(1, int(n * probability))
        positions = rng.choice(max(1, n - 1), size=min(count, n - 1), replace=False)
        for pos in positions.tolist():
            table_id[pos], table_id[pos + 1] = table_id[pos + 1], table_id[pos]
            row[pos], row[pos + 1] = row[pos + 1], row[pos]

    def _order(
        self, triggers: np.ndarray[Any, Any] | None, buffer: int
    ) -> tuple[np.ndarray[Any, Any], np.ndarray[Any, Any]]:
        """The output order: ``(primary event index, is replay)`` per event sent. After a primary
        event ``k`` that triggers a replay, the last ``min(burst, window)`` primary events up to
        ``k`` are sent again."""
        n = self.primary_events
        if triggers is None or not triggers.any():
            return np.arange(n), np.zeros(n, dtype=bool)
        refs: list[np.ndarray[Any, Any]] = []
        flags: list[np.ndarray[Any, Any]] = []
        prev = 0
        for k in np.flatnonzero(triggers).tolist():
            refs.append(np.arange(prev, k + 1))
            flags.append(np.zeros(k + 1 - prev, dtype=bool))
            held = min(k + 1, buffer)
            burst = min(self._cfg.replay_burst_size, held)
            refs.append(np.arange(k - burst + 1, k + 1))
            flags.append(np.ones(burst, dtype=bool))
            prev = k + 1
        refs.append(np.arange(prev, n))
        flags.append(np.zeros(n - prev, dtype=bool))
        return np.concatenate(refs), np.concatenate(flags)

    # ---- the runtime's view -------------------------------------------------------------

    def fingerprint(self) -> str:
        cfg = self._cfg
        document = {
            "tables": {
                t: [self._tables[t].num_rows, str(self._tables[t].schema)] for t in self._names
            },
            "topics": self._topics,
            "config": [
                cfg.out_of_order_probability,
                cfg.replay_enabled,
                cfg.replay_probability,
                cfg.replay_burst_size,
                cfg.max_events,
                cfg.seed,
                cfg.envelope_source,
                cfg.envelope_schema_version,
            ],
            "events": self.total_events,
            "digest": hashlib.sha256(self._table_id.tobytes() + self._row.tobytes()).hexdigest(),
        }
        return hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()

    def jitter_seconds(self, start: int, count: int) -> float:
        """Total pause for the events ``start .. start + count - 1`` of the output."""
        if self._jitter is None:
            return 0.0
        span = slice(start, start + count)
        refs = self._out_ref[span]
        return float(self._jitter[refs][~self._out_replay[span]].sum())

    def blocks(self, offset: int = 0) -> Iterator[EventBlock]:
        if offset < 0:
            raise ValueError("offset must be 0 or more")
        pos = offset
        while pos < self.total_events:
            end = min(pos + BLOCK_EVENTS, self.total_events)
            refs = self._out_ref[pos:end]
            key = self._table_id[refs] * 2 + self._out_replay[pos:end]
            change = np.flatnonzero(key[1:] != key[:-1])
            if len(change):
                end = pos + int(change[0]) + 1
            yield self._block(pos, end)
            pos = end

    def _block(self, start: int, end: int) -> EventBlock:
        refs = self._out_ref[start:end]
        name = self._names[int(self._table_id[refs[0]])]
        replay = bool(self._out_replay[start])
        seq = self._row[refs]
        taken = self._tables[name].take(pa.array(seq))
        batch = pa.RecordBatch.from_arrays(
            [c.combine_chunks() for c in taken.columns], schema=taken.schema
        )
        check_reserved(batch.schema, name)
        arrays = list(batch.columns)
        names = list(batch.schema.names)
        table_col, seq_col = event_columns(name, pa.array(seq))
        arrays += [table_col, seq_col]
        names += [FIELD_TABLE, FIELD_SEQ]
        time_col = event_time_column(batch.schema)
        if time_col is not None:
            arrays.append(batch.column(time_col))
            names.append(FIELD_TIME)
        flat = pa.RecordBatch.from_arrays(arrays, names=names)
        return EventBlock(start, name, self._envelope(flat, name, seq, replay))

    def _envelope(
        self, flat: pa.RecordBatch, table: str, seq: np.ndarray[Any, Any], replay: bool
    ) -> pa.RecordBatch:
        n = flat.num_rows
        cfg = self._cfg
        topic = self._topics.get(table, table)
        sent = datetime.now(UTC).isoformat().replace("+00:00", "Z")

        def const(value: str) -> pa.Array:
            return pa.repeat(value, n).cast(pa.string())

        columns: list[tuple[str, pa.Array]] = [
            ("specversion", const("1.0")),
            ("id", pa.array([f"{table}/{s}" for s in seq.tolist()], pa.string())),
            ("source", const(cfg.envelope_source)),
            ("type", const(f"shape.{topic}")),
            ("time", const(sent)),
            ("datacontenttype", const("application/json")),
            ("topic", const(topic)),
            ("schemaversion", const(cfg.envelope_schema_version)),
            ("correlationid", const(self._correlation_id)),
            ("shapetable", const(table)),
            ("shapeseq", pa.array(seq, pa.int64())),
        ]
        if replay:
            columns.append(("replay", pa.array([True] * n, pa.bool_())))
            columns.append(("replaytime", const(sent)))
        columns.append(("data", pa.array(rows_of(flat))))
        return pa.RecordBatch.from_arrays(
            [c for _, c in columns], names=[name for name, _ in columns]
        )


class _Borrowed:
    """A sink the caller owns: the runtime's ``close`` only flushes it."""

    def __init__(self, sink: EventSink) -> None:
        self._sink = sink

    def send(self, batch: pa.RecordBatch) -> None:
        self._sink.send(batch)

    def flush(self) -> None:
        self._sink.flush()

    def close(self) -> None:
        self._sink.flush()


class _Jittered:
    """Pauses for the jitter of the events of each batch before delivering it."""

    def __init__(self, sink: EventSink, plan: TablesEventPlan) -> None:
        self._sink = sink
        self._plan = plan
        self._cursor = 0

    def send(self, batch: pa.RecordBatch) -> None:
        pause = self._plan.jitter_seconds(self._cursor, batch.num_rows)
        self._cursor += batch.num_rows
        if pause > 0:
            time.sleep(pause)
        self._sink.send(batch)

    def flush(self) -> None:
        self._sink.flush()

    def close(self) -> None:
        self._sink.close()


class StreamEmitter:
    """Emit tables as enveloped streaming events.

    Args:
        tables: ``{table name: table}`` (Arrow tables, pandas frames or a generation result).
        config: What to emit and how.
        sink: A sink to deliver to (an ``EventSink``: ``send(batch)``, ``flush()``, ``close()``);
            the emitter flushes it and leaves it open. By default one is built from
            ``config.sink_type``.
        correlation_id: The ``correlationid`` of every event (a new id for each emitter by
            default); the hybrid simulator passes its run id.

    Example::

        cfg = StreamEmitConfig(rate_per_sec=50, topics=["orders", "returns"], sink_type="file",
                               sink_connection={"path": "events.jsonl"})
        result = StreamEmitter(tables=generated.tables, config=cfg).emit()
    """

    def __init__(
        self,
        tables: Any,
        config: StreamEmitConfig | None = None,
        sink: EventSink | None = None,
        *,
        correlation_id: str | None = None,
    ) -> None:
        self._tables = tb.as_tables(tables)
        self._config = config or StreamEmitConfig()
        self._sink = sink
        self._rng = np.random.default_rng(self._config.seed)
        self._correlation_id = correlation_id or str(uuid.uuid4())
        self._schema_versions: dict[str, str] = {}
        self._emits = 0
        if sink is None:  # a bad sink type is an error now, not at the first event
            self._check_sink_type(self._config)

    # ---- public API ---------------------------------------------------------------------

    def emit(self, tables: Any = None, config: StreamEmitConfig | None = None) -> StreamEmitResult:
        """Emit every row of ``tables`` (default: the emitter's) and return what was sent."""
        source = tb.as_tables(tables) if tables else self._tables
        cfg = config or self._config
        topics = topic_map(source, cfg)
        for topic in topics.values():
            self._schema_versions[topic] = cfg.envelope_schema_version
        buffer = max(100, int(cfg.rate_per_sec * cfg.replay_window_minutes * 60))
        plan = TablesEventPlan(source, cfg, topics, self._rng, self._correlation_id, buffer)

        sink = self._open_sink(cfg)
        if cfg.jitter_ms > 0 and cfg.realtime:
            sink = _Jittered(sink, plan)
        paced = cfg.realtime and cfg.rate_per_sec > 0
        runtime = EmitConfig(
            realtime=paced,
            rate=cfg.rate_per_sec if paced else 100.0,
            bursts=tuple(b.to_burst() for b in cfg.burst_windows) if paced else (),
        )
        started = time.time()
        EmitRunner(plan, sink, runtime).run()
        self._emits += 1
        return StreamEmitResult(
            events_sent=plan.primary_events,
            replay_events_sent=plan.replay_events,
            topics_used=set(topics.values()),
            elapsed_seconds=time.time() - started,
            schema_versions=dict(self._schema_versions),
        )

    # ---- sinks --------------------------------------------------------------------------

    @staticmethod
    def _check_sink_type(cfg: StreamEmitConfig) -> None:
        kind = cfg.sink_type.lower()
        if kind in ("console", "file") or "://" in kind:
            return
        raise ValueError(
            f"Unknown sink_type {cfg.sink_type!r}. Supported: console, file, or the URI of an "
            "emitter (kafka://, eventhubs://, eventstream://, ...)."
        )

    def _open_sink(self, cfg: StreamEmitConfig) -> EventSink:
        if self._sink is not None:
            return _Borrowed(self._sink)
        self._check_sink_type(cfg)
        kind = cfg.sink_type.lower()
        if kind == "console":
            return StdoutSink(envelope="flat")
        if kind == "file":
            path = cfg.sink_connection.get("path", "events.jsonl")
            append = cfg.sink_connection.get("mode", "w") == "a" or self._emits > 0
            return FileSink(path, envelope="flat", append=append)
        return open_sink(cfg.sink_type, envelope="flat")
