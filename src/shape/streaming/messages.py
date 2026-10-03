"""Broker messages to Arrow micro-batches (P3-04).

The stream-source plugins (``shape-kafka``, ``shape-eventhubs``) turn what their client library
delivers into ``StreamMessage`` values and call :func:`decode_messages`; the decoding rules live
here once, so every source gives the profiler the same batches.

A message body is one JSON object (UTF-8 text or bytes). Its top-level fields become columns; a
nested object or array is kept as its JSON text. Besides the payload the batch carries
``_shape_event_time`` (timestamp, microseconds, UTC), the column the stream runtime windows on:

* the payload field named by ``event_time_field`` (default ``_shape_event_time``), an ISO-8601
  string or a number in ``event_time_unit``, when it holds a valid time;
* otherwise the broker's timestamp (Kafka message time, Event Hubs enqueued time);
* otherwise null (the runtime counts rows without an event time).

Without a ``schema`` the column types come from the batch, with fields in order of first
appearance; a column whose values do not agree on a type becomes a string column. With a
``schema`` (a source freezes the first batch's schema for the rest of its read, and a profiler
passes its own), a row whose value cannot take its column's type is *rejected*: dropped and
counted, never coerced. A message that is not a JSON object is *undecodable*: dropped and
counted. Offsets still advance past both, so a poison message is not read twice.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError

EVENT_TIME = "_shape_event_time"
PARTITION = "_shape_partition"
OFFSET = "_shape_offset"
PARTITION_KEY = b"shape.partition"  # schema metadata of a batch that came from one partition
_UNITS = {"s": 1_000_000, "ms": 1_000, "us": 1}
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
EVENT_TIME_TYPE = pa.timestamp("us", tz="UTC")
_MIN_US, _MAX_US = -(2**63), 2**63 - 1


class StreamSourceError(ShapeError):
    """A stream source cannot do what it was asked (bad URI, missing client library, a message
    that cannot be decoded when ``on_error="raise"``)."""


@dataclass(frozen=True, slots=True)
class StreamMessage:
    """One message as the broker delivered it.

    ``partition`` is the broker's partition id as text, ``offset`` its position in the
    partition (Kafka offset, Event Hubs sequence number), ``timestamp_us`` the broker time in
    microseconds since the epoch, ``value`` the body.
    """

    partition: str
    offset: int
    value: bytes | str | None
    timestamp_us: int | None = None


@dataclass(slots=True)
class DecodeStats:
    """What a source read did, for reports and tests."""

    messages: int = 0
    rows: int = 0
    undecodable: int = 0
    rejected: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "messages": self.messages,
            "rows": self.rows,
            "undecodable": self.undecodable,
            "rejected": self.rejected,
        }


def _event_us(value: Any, unit: str) -> int | None:
    """A payload value as microseconds since the epoch, or ``None`` when it is not a time."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        us = int(value * _UNITS[unit])
        return us if _MIN_US <= us <= _MAX_US else None  # a timestamp column holds int64
    if isinstance(value, str):
        try:
            when = datetime.fromisoformat(value)
        except ValueError:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        delta = when - _EPOCH
        return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds
    return None


def _plain(value: Any) -> Any:
    """Nested JSON values are kept as text so every column is flat."""
    if isinstance(value, dict | list):
        return json.dumps(value, separators=(",", ":"), sort_keys=True)
    return value


def _inferred(values: list[Any]) -> pa.Array:
    try:
        return pa.array(values)
    except (pa.ArrowInvalid, pa.ArrowTypeError, OverflowError):
        return pa.array(
            [None if v is None else v if isinstance(v, str) else json.dumps(v) for v in values],
            type=pa.string(),
        )


def _typed(values: list[Any], typ: pa.DataType) -> tuple[pa.Array, list[int]]:
    """``values`` as ``typ``, and the indexes of the values that could not take it (set null)."""
    try:
        return pa.array(values, type=typ), []
    except (pa.ArrowInvalid, pa.ArrowTypeError, OverflowError):
        pass
    bad: list[int] = []
    kept: list[Any] = []
    for i, v in enumerate(values):
        try:
            pa.array([v], type=typ)
        except (pa.ArrowInvalid, pa.ArrowTypeError, OverflowError):
            bad.append(i)
            kept.append(None)
        else:
            kept.append(v)
    return pa.array(kept, type=typ), bad


def decode_messages(
    messages: Iterable[StreamMessage],
    *,
    schema: pa.Schema | None = None,
    event_time_field: str = EVENT_TIME,
    event_time_unit: str = "ms",
    with_offsets: bool = False,
    stats: DecodeStats | None = None,
    on_error: str = "skip",
) -> pa.RecordBatch | None:
    """Decode messages into one record batch, or ``None`` when no message gave a row.

    ``with_offsets`` adds ``_shape_partition`` (string) and ``_shape_offset`` (int64), for
    row-level deduplication on offset. ``on_error="raise"`` turns the first undecodable
    message or rejected row into a ``StreamSourceError``.
    """
    if event_time_unit not in _UNITS:
        raise ValueError(f"event_time_unit must be one of {sorted(_UNITS)}")
    if on_error not in ("skip", "raise"):
        raise ValueError("on_error must be 'skip' or 'raise'")
    stats = stats if stats is not None else DecodeStats()
    rows: list[dict[str, Any]] = []
    times: list[int | None] = []
    source: list[StreamMessage] = []
    for msg in messages:
        stats.messages += 1
        body = msg.value
        try:
            if body is None:
                raise ValueError("empty message")
            obj = json.loads(body)
            if not isinstance(obj, dict):
                raise ValueError("the message is not a JSON object")
        except (ValueError, UnicodeDecodeError, RecursionError) as exc:
            stats.undecodable += 1
            if on_error == "raise":
                raise StreamSourceError(
                    f"partition {msg.partition} offset {msg.offset}: {exc}"
                ) from exc
            continue
        when = _event_us(obj.pop(event_time_field, None), event_time_unit)
        if event_time_field != EVENT_TIME:
            obj.pop(EVENT_TIME, None)
        rows.append({k: _plain(v) for k, v in obj.items()})
        times.append(when if when is not None else msg.timestamp_us)
        source.append(msg)
    if not rows:
        return None

    if schema is None:
        names: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    names.append(key)
        names = [n for n in names if n not in (PARTITION, OFFSET) or not with_offsets]
        columns = {n: _inferred([r.get(n) for r in rows]) for n in names}
        bad: set[int] = set()
    else:
        columns = {}
        bad = set()
        for f in schema:
            if f.name == EVENT_TIME or (with_offsets and f.name in (PARTITION, OFFSET)):
                continue
            arr, failed = _typed([r.get(f.name) for r in rows], f.type)
            columns[f.name] = arr
            bad.update(failed)
    columns[EVENT_TIME] = pa.array(times, type=EVENT_TIME_TYPE)
    if with_offsets:
        columns[PARTITION] = pa.array([m.partition for m in source], type=pa.string())
        columns[OFFSET] = pa.array([m.offset for m in source], type=pa.int64())
    batch = pa.RecordBatch.from_pydict(columns)
    if schema is not None:
        batch = batch.select([f.name for f in schema])
        batch = conform(batch, schema)
    if bad:
        stats.rejected += len(bad)
        if on_error == "raise":
            first = source[min(bad)]
            raise StreamSourceError(
                f"partition {first.partition} offset {first.offset}: "
                "a value does not fit the schema"
            )
        keep = [i for i in range(batch.num_rows) if i not in bad]
        batch = batch.take(pa.array(keep, type=pa.int64()))
    stats.rows += batch.num_rows
    if not batch.num_rows:
        return None
    origin = {m.partition for m in source}
    if len(origin) == 1:  # the consumer keeps one watermark per partition (see ``partition_of``)
        batch = batch.replace_schema_metadata({PARTITION_KEY: next(iter(origin)).encode()})
    return batch


def partition_of(batch: pa.RecordBatch) -> str | None:
    """The partition every row of ``batch`` came from, when its source said so (``decode_messages``
    does, for a batch from one partition); ``None`` for a batch of unknown or mixed origin."""
    meta = batch.schema.metadata
    value = None if not meta else meta.get(PARTITION_KEY)
    return None if value is None else value.decode()


def conform(batch: pa.RecordBatch, schema: pa.Schema) -> pa.RecordBatch:
    """``batch`` with ``schema``'s column types (same column names, same order); the batch keeps
    its own schema metadata (its partition)."""
    return pa.RecordBatch.from_arrays(
        [batch.column(i).cast(f.type) for i, f in enumerate(schema)],
        schema=schema.with_metadata(batch.schema.metadata or {}),
    )


def freeze(batch: pa.RecordBatch) -> pa.Schema:
    """The schema a source keeps for the rest of its read: a column that was all null in the
    first batch is read as a string column from then on."""
    fields = [
        pa.field(f.name, pa.string()) if pa.types.is_null(f.type) else f for f in batch.schema
    ]
    return pa.schema(fields)


def partition_offsets(positions: Mapping[str, int]) -> dict[str, int]:
    """The ``StreamOffset`` value for a source's positions: ``{partition: next offset}``, which
    ``StreamConsumer`` reads for deduplication (partitions in sorted order, so the JSON is
    stable)."""
    return {str(p): int(positions[p]) for p in sorted(positions, key=_sort_key)}


def _sort_key(p: str) -> tuple[int, int | str]:
    return (0, int(p)) if p.isdigit() else (1, p)


def group_by_partition(messages: Sequence[StreamMessage]) -> list[list[StreamMessage]]:
    """Split a poll's messages by partition (partitions in order, messages in arrival order)."""
    groups: dict[str, list[StreamMessage]] = {}
    for m in messages:
        groups.setdefault(m.partition, []).append(m)
    return [groups[p] for p in sorted(groups, key=_sort_key)]
