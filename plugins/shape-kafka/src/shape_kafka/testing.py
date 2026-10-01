"""An in-memory broker with the slice of ``confluent_kafka.Consumer`` the source uses.

For contract tests and the plugin kit: ``FakeBroker.source()`` is a ``KafkaStreamSource`` that
reads the broker's topics, and ``fail_at`` makes ``consume`` report a transport error once the given
number of messages have been delivered (to exercise reconnects), then every ``fail_every``.
"""

from __future__ import annotations

import json
from collections import namedtuple
from collections.abc import Mapping, Sequence
from typing import Any

from .source import KafkaStreamSource

FakeTopicPartition = namedtuple("FakeTopicPartition", "topic partition offset")  # noqa: PYI024


class FakeError:
    def __init__(self, name: str, retriable: bool = False) -> None:
        self._name = name
        self._retriable = retriable

    def name(self) -> str:
        return self._name

    def retriable(self) -> bool:
        return self._retriable

    def __str__(self) -> str:
        return self._name


class FakeMessage:
    def __init__(
        self, partition: int, offset: int, value: bytes | None, ts_ms: int | None, error: Any = None
    ) -> None:
        self._p, self._o, self._v, self._ts, self._e = partition, offset, value, ts_ms, error

    def error(self) -> Any:
        return self._e

    def partition(self) -> int:
        return self._p

    def offset(self) -> int:
        return self._o

    def value(self) -> bytes | None:
        return self._v

    def timestamp(self) -> tuple[int, int]:
        return (1, self._ts) if self._ts is not None else (0, -1)


class _Topic:
    def __init__(self, partitions: Mapping[int, Any]) -> None:
        self.partitions = dict(partitions)
        self.error = None


class _Metadata:
    def __init__(self, topics: Mapping[str, _Topic]) -> None:
        self.topics = dict(topics)


class FakeConsumer:
    def __init__(self, broker: FakeBroker, config: Mapping[str, Any]) -> None:
        self.broker = broker
        self.config = dict(config)
        self.assigned: list[FakeTopicPartition] = []
        self.next: dict[int, int] = {}
        self.closed = False
        broker.consumers.append(self)

    def list_topics(self, topic: str, timeout: float = 0) -> _Metadata:
        if topic not in self.broker.topics:
            return _Metadata({})
        return _Metadata({topic: _Topic(self.broker.topics[topic])})

    def get_watermark_offsets(
        self, tp: Any, timeout: float = 0, cached: bool = False
    ) -> tuple[int, int]:
        return 0, len(self.broker.topics[tp.topic][tp.partition])

    def assign(self, tps: Sequence[Any]) -> None:
        self.assigned = list(tps)
        self.topic = self.assigned[0].topic
        self.broker.connections += 1
        self.next = {tp.partition: tp.offset for tp in self.assigned}

    def consume(self, num_messages: int = 1, timeout: float = -1) -> list[FakeMessage]:
        b = self.broker
        if b.fail_at is not None and b.delivered >= b.fail_at:
            b.fail_at = b.next_fail()
            return [FakeMessage(-1, -1, None, None, FakeError("_TRANSPORT"))]
        cap = min(num_messages, b.chunk) if b.chunk else num_messages
        out: list[FakeMessage] = []
        for p in sorted(self.next):
            log = b.topics[self.topic][p]
            while len(out) < cap and self.next[p] < len(log):
                value, ts = log[self.next[p]]
                out.append(FakeMessage(p, self.next[p], value, ts))
                self.next[p] += 1
        b.delivered += len(out)
        return out

    def position(self, tps: Sequence[Any]) -> list[FakeTopicPartition]:
        return [FakeTopicPartition(tp.topic, tp.partition, self.next[tp.partition]) for tp in tps]

    def close(self) -> None:
        self.closed = True


class FakeBroker:
    """``topics`` maps a topic to ``{partition: [(value bytes, timestamp ms or None), ...]}``."""

    def __init__(
        self,
        topics: Mapping[str, Mapping[int, Sequence[tuple[bytes | None, int | None]]]],
        *,
        chunk: int = 0,
        fail_at: int | None = None,
        fail_every: int | None = None,
    ) -> None:
        self.topics = {t: {p: list(m) for p, m in parts.items()} for t, parts in topics.items()}
        self.chunk = chunk
        self.fail_at = fail_at
        self.fail_every = fail_every
        self.delivered = 0
        self.connections = 0
        self.consumers: list[FakeConsumer] = []

    def next_fail(self) -> int | None:
        if self.fail_every is None:
            return None
        return self.delivered + self.fail_every

    def consumer(self, config: Mapping[str, Any]) -> FakeConsumer:
        return FakeConsumer(self, config)

    def source(self) -> KafkaStreamSource:
        return KafkaStreamSource(self.consumer, FakeTopicPartition)


def json_messages(
    rows: Sequence[Mapping[str, Any]], start_ms: int | None = 1_700_000_000_000, step_ms: int = 10
) -> list[tuple[bytes, int | None]]:
    """JSON-object messages for ``rows``, with Kafka timestamps ``step_ms`` apart."""
    return [
        (json.dumps(r).encode(), None if start_ms is None else start_ms + i * step_ms)
        for i, r in enumerate(rows)
    ]
