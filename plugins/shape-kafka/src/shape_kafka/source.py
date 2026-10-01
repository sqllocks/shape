"""The ``kafka://`` stream source (P3-04).

``kafka://broker1:9092,broker2:9092/topic`` reads one topic. Shape keeps its own position: the
consumer is *assigned* every partition at explicit offsets (no consumer group, nothing is
committed to the broker), so a ``StreamOffset`` is ``{partition: next offset}`` and a
checkpoint resumes exactly where it left off.

Options of :meth:`KafkaStreamSource.read`:

``start_at``       ``"earliest"`` (default) or ``"latest"``: where partitions without a start
                   offset begin (a partition missing from ``start`` is read from here).
``stop_at_end``    ``True`` (default): stop once every partition has reached the end it had when
                   the read began. ``False`` follows the topic until ``idle_timeout`` or
                   ``max_messages``, or forever. A bounded read takes the partitions one after
                   the other, in order, so every run gives the same batches; following reads
                   them together, in arrival order.
``idle_timeout``   stop after this many seconds without a message (``None``: keep waiting).
``max_messages``   stop after this many messages.
``batch_size``     messages per micro-batch, at most (default 65,536); a batch never mixes
                   partitions.
``config``         extra ``confluent-kafka`` consumer settings (security, SASL, ...); Shape
                   sets ``bootstrap.servers`` itself and keeps ``enable.auto.commit`` off.
``schema``         the column types to read into; by default those of the first batch.
``on_error``       ``"skip"`` (default) counts undecodable messages, ``"raise"`` fails.

The remaining options (``event_time_field``, ``event_time_unit``, ``with_offsets``) are those of
``shape.streaming.messages.decode_messages``.

The ``confluent-kafka`` library is imported when a read starts, never at plugin load.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping
from typing import Any, NoReturn
from urllib.parse import unquote, urlsplit

import pyarrow as pa

from shape.plugins.api.v1 import StreamOffset
from shape.streaming.messages import (
    DecodeStats,
    StreamMessage,
    StreamSourceError,
    conform,
    decode_messages,
    freeze,
    group_by_partition,
    partition_offsets,
)

DEFAULT_BATCH = 65_536
_POLL = 1.0  # seconds one poll waits for messages
_METADATA_TIMEOUT = 10.0
# librdkafka error names that mean "the connection is gone for now": the consumer reconnects.
_TRANSIENT = frozenset(
    {"_TRANSPORT", "_ALL_BROKERS_DOWN", "_TIMED_OUT", "_RESOLVE", "_TIMED_OUT_QUEUE", "_DESTROY"}
)


def parse_uri(uri: str) -> tuple[str, str]:
    """``(bootstrap servers, topic)`` of a ``kafka://`` URI."""
    parts = urlsplit(uri)
    if parts.scheme != "kafka" or not parts.netloc:
        raise StreamSourceError(f"not a kafka URI: {uri!r} (kafka://host:9092[,host2:9092]/topic)")
    topic = unquote(parts.path.lstrip("/"))
    if not topic or "/" in topic:
        raise StreamSourceError(f"the kafka URI needs one topic name: {uri!r}")
    return parts.netloc, topic


def _confluent_consumer(config: dict[str, Any]) -> Any:
    try:
        from confluent_kafka import Consumer
    except ImportError as exc:
        raise StreamSourceError(
            "reading Kafka needs the confluent-kafka package (pip install sqllocks-shape-kafka)"
        ) from exc
    return Consumer(config)


def _topic_partition(topic: str, partition: int, offset: int) -> Any:
    from confluent_kafka import TopicPartition

    return TopicPartition(topic, partition, offset)


class KafkaStreamSource:
    """A Kafka topic as ``(offset, record batch)`` pairs.

    ``consumer_factory`` and ``partition_factory`` are for tests: they stand in for
    ``confluent_kafka.Consumer`` and ``TopicPartition``.
    """

    name = "kafka"
    schemes = ("kafka",)

    def __init__(
        self,
        consumer_factory: Callable[[dict[str, Any]], Any] | None = None,
        partition_factory: Callable[[str, int, int], Any] | None = None,
    ) -> None:
        self._consumer_factory = consumer_factory or _confluent_consumer
        self._partition_factory = partition_factory or _topic_partition
        self.stats = DecodeStats()

    def can_open(self, uri: str) -> bool:
        try:
            parse_uri(uri)
        except StreamSourceError:
            return False
        return True

    # ------------------------------------------------------------------ read
    def read(
        self, uri: str, start: StreamOffset | None = None, **options: Any
    ) -> Iterator[tuple[StreamOffset, pa.RecordBatch]]:
        """Yield each batch with the offsets just after it."""
        servers, topic = parse_uri(uri)
        opts = dict(options)
        start_at = opts.pop("start_at", "earliest")
        stop_at_end = bool(opts.pop("stop_at_end", True))
        idle_timeout = opts.pop("idle_timeout", None)
        max_messages = opts.pop("max_messages", None)
        batch_size = int(opts.pop("batch_size", DEFAULT_BATCH))
        config = dict(opts.pop("config", None) or {})
        schema: pa.Schema | None = opts.pop("schema", None)
        decode: dict[str, Any] = {
            k: opts.pop(k)
            for k in ("event_time_field", "event_time_unit", "with_offsets", "on_error")
            if k in opts
        }
        if opts:
            raise StreamSourceError(f"unknown kafka source options: {sorted(opts)}")
        if start_at not in ("earliest", "latest"):
            raise StreamSourceError("start_at must be 'earliest' or 'latest'")
        if batch_size < 1:
            raise StreamSourceError("batch_size must be positive")
        config.update(
            {
                "bootstrap.servers": servers,
                "enable.auto.commit": False,
                "group.id": config.get("group.id", "shape-stream-profile"),
            }
        )
        consumer = self._consumer_factory(config)
        try:
            yield from self._read(
                consumer,
                topic,
                start,
                start_at=start_at,
                stop_at_end=stop_at_end,
                idle_timeout=idle_timeout,
                max_messages=max_messages,
                batch_size=batch_size,
                schema=schema,
                decode=decode,
            )
        finally:
            consumer.close()

    def _read(
        self,
        consumer: Any,
        topic: str,
        start: StreamOffset | None,
        *,
        start_at: str,
        stop_at_end: bool,
        idle_timeout: float | None,
        max_messages: int | None,
        batch_size: int,
        schema: pa.Schema | None,
        decode: Mapping[str, Any],
    ) -> Iterator[tuple[StreamOffset, pa.RecordBatch]]:
        info = consumer.list_topics(topic, timeout=_METADATA_TIMEOUT).topics.get(topic)
        if info is None or getattr(info, "error", None) is not None or not info.partitions:
            raise StreamSourceError(f"the topic {topic!r} does not exist or has no partitions")
        ids = sorted(int(p) for p in info.partitions)
        given = {str(k): int(v) for k, v in (start.value if start is not None else {}).items()}
        positions: dict[str, int] = {}
        ends: dict[str, int] = {}
        for p in ids:
            tp = self._partition_factory(topic, p, -1)
            low, high = consumer.get_watermark_offsets(tp, timeout=_METADATA_TIMEOUT)
            if str(p) in given:
                # A checkpoint older than the broker's retention starts at what is left.
                positions[str(p)] = min(max(given[str(p)], low), high)
            else:
                positions[str(p)] = low if start_at == "earliest" else high
            ends[str(p)] = high
        # A bounded read takes the partitions one after the other, in order, so the batches (and
        # the profile built from them) are the same on every run; following a topic reads all
        # partitions together, in arrival order.
        groups = [[p] for p in ids] if stop_at_end else [ids]
        total = 0
        for group in groups:
            keys = [str(p) for p in group]
            consumer.assign([self._partition_factory(topic, p, positions[str(p)]) for p in group])
            last = time.monotonic()
            while True:
                if max_messages is not None and total >= max_messages:
                    return
                if stop_at_end and all(positions[k] >= ends[k] for k in keys):
                    break
                want = batch_size if max_messages is None else min(batch_size, max_messages - total)
                polled = consumer.consume(num_messages=want, timeout=_POLL)
                messages: list[StreamMessage] = []
                for raw in polled:
                    err = raw.error()
                    if err is not None:
                        self._raise(err)
                    _kind, ts = raw.timestamp()
                    messages.append(
                        StreamMessage(
                            str(raw.partition()),
                            int(raw.offset()),
                            raw.value(),
                            None if ts is None or ts < 0 else int(ts) * 1000,
                        )
                    )
                if not messages:
                    if stop_at_end:
                        # Transaction markers and compacted-away offsets are never delivered
                        # but do move the consumer's position; the end may already be behind it.
                        for tp in consumer.position(
                            [self._partition_factory(topic, p, positions[str(p)]) for p in group]
                        ):
                            positions[str(tp.partition)] = max(
                                positions[str(tp.partition)], int(tp.offset)
                            )
                    if idle_timeout is not None and time.monotonic() - last >= idle_timeout:
                        return
                    continue
                last = time.monotonic()
                total += len(messages)
                for part in group_by_partition(messages):
                    key = part[0].partition
                    positions[key] = max(positions[key], part[-1].offset + 1)
                    batch = decode_messages(part, schema=schema, stats=self.stats, **decode)
                    if batch is None:
                        continue
                    if schema is None:
                        schema = freeze(batch)
                        batch = conform(batch, schema)
                    yield StreamOffset(partition_offsets(positions)), batch

    @staticmethod
    def _raise(err: Any) -> NoReturn:
        name = err.name() if hasattr(err, "name") else ""
        text = f"kafka: {err}"
        if name in _TRANSIENT or (hasattr(err, "retriable") and err.retriable()):
            raise ConnectionError(text)
        raise StreamSourceError(text)
