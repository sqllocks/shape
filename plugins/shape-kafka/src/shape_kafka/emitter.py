"""The ``kafka://`` emitter (P5-02).

``kafka://broker1:9092,broker2:9092/topic`` sends each event as one message: the body is the
event's JSON (flat, or the CloudEvents envelope), the **message key is the D-12 idempotency key**
``<table>/<seq>`` (so a replay of a row lands in the same partition with the same key, and a
log-compacted topic keeps one message per row), and the header ``shape-table`` names the table.

Delivery is at-least-once. ``emit`` returns after the broker has acknowledged every message of
its batches (``acks=all``, idempotent producer): a failed delivery raises ``ConnectionError``,
which the runtime retries, so a repeated message is possible and a lost one is not. A full local
queue is waited for (the producer is polled until it drains), which is the backpressure the
runtime passes back to the generator. A message the broker did not acknowledge within
``flush_timeout`` seconds raises ``TimeoutError``.

Options of :meth:`KafkaEmitter.emit`:

``envelope``       ``"flat"`` (default) or ``"cloudevents"``.
``config``         extra ``confluent-kafka`` producer settings (security, SASL, compression, ...);
                   ``bootstrap.servers`` comes from the URI. Defaults: ``acks=all``,
                   ``enable.idempotence=true``, ``linger.ms=5``.
``flush_timeout``  seconds to wait for acknowledgements (default 60).
``resuming``       accepted and ignored (a topic has no file to continue).

``confluent-kafka`` is imported when the first message is sent, never at plugin load.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from typing import Any
from urllib.parse import unquote, urlsplit

import pyarrow as pa

from shape.errors import ShapeError
from shape.streaming.emit.formats import ENVELOPES, encode_events

DEFAULT_CONFIG: dict[str, Any] = {"acks": "all", "enable.idempotence": True, "linger.ms": 5}
HEADER_TABLE = "shape-table"
HEADER_SYNTHETIC = "shape-synthetic"
_POLL = 0.05  # seconds one wait for a full queue to drain


def parse_uri(uri: str) -> tuple[str, str]:
    """``(bootstrap servers, topic)`` of a ``kafka://`` URI."""
    parts = urlsplit(uri)
    if parts.scheme != "kafka" or not parts.netloc:
        raise ShapeError(f"not a kafka URI: {uri!r} (kafka://host:9092[,host2:9092]/topic)")
    topic = unquote(parts.path.lstrip("/"))
    if not topic or "/" in topic:
        raise ShapeError(f"the kafka URI needs one topic name: {uri!r}")
    return parts.netloc, topic


def _confluent_producer(config: dict[str, Any]) -> Any:
    try:
        from confluent_kafka import Producer
    except ImportError as exc:
        raise ShapeError(
            "emitting to Kafka needs the confluent-kafka package (pip install sqllocks-shape-kafka)"
        ) from exc
    return Producer(config)


class KafkaEmitter:
    """Events to a Kafka topic, keyed by the idempotency key.

    ``producer_factory(config)`` is for tests: it stands in for ``confluent_kafka.Producer``.
    """

    name = "kafka"
    accepts_poison = True  # one JSON text per message: a cut-off body is a poison message
    supports_synthetic = True  # the `synthetic` option marks every message with a header
    schemes = ("kafka",)

    def __init__(self, producer_factory: Callable[[dict[str, Any]], Any] | None = None) -> None:
        self._factory = producer_factory or _confluent_producer
        self._producers: dict[str, Any] = {}

    def _producer(self, servers: str, extra: Mapping[str, Any] | None) -> Any:
        config = {**DEFAULT_CONFIG, **(extra or {}), "bootstrap.servers": servers}
        key = json.dumps(config, sort_keys=True, default=str)
        producer = self._producers.get(key)
        if producer is None:
            producer = self._producers[key] = self._factory(config)
        return producer

    def emit(
        self,
        uri: str,
        batches: Iterable[pa.RecordBatch],
        *,
        envelope: str = "flat",
        resuming: bool = False,
        config: Mapping[str, Any] | None = None,
        flush_timeout: float = 60.0,
        synthetic: bool = False,
        **options: Any,
    ) -> int:
        """Send every batch; return the number of events, after the broker acknowledged them."""
        if options:
            raise ShapeError(f"unknown kafka emitter options: {sorted(options)}")
        if envelope not in ENVELOPES:
            raise ShapeError(f"unknown envelope {envelope!r}; choose from {', '.join(ENVELOPES)}")
        servers, topic = parse_uri(uri)
        producer = self._producer(servers, config)
        failures: list[Any] = []

        def delivered(err: Any, _msg: Any) -> None:
            if err is not None:
                failures.append(err)

        sent = 0
        marker = [(HEADER_SYNTHETIC, b"true")] if synthetic else []
        for batch in batches:
            for event in encode_events(batch, envelope):
                while True:
                    try:
                        producer.produce(
                            topic,
                            value=event.body,
                            key=event.key.encode("utf-8"),
                            headers=[(HEADER_TABLE, event.table.encode("utf-8")), *marker],
                            on_delivery=delivered,
                        )
                        break
                    except BufferError:
                        # The local queue is full: serve delivery reports until it drains.
                        producer.poll(_POLL)
                sent += 1
            producer.poll(0)
        pending = producer.flush(flush_timeout)
        if failures:
            raise ConnectionError(
                f"kafka: {len(failures)} of {sent} messages were not delivered to {topic!r}: "
                f"{failures[0]}"
            )
        if pending:
            raise TimeoutError(
                f"kafka: {pending} messages to {topic!r} not acknowledged in {flush_timeout}s"
            )
        return sent

    def flush(self) -> None:
        for producer in self._producers.values():
            producer.flush(60.0)

    def close(self) -> None:
        self.flush()
        self._producers.clear()
