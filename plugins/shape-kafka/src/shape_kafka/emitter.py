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

``envelope``       ``"flat"`` (default) or ``"cloudevents"`` (JSON only).
``event_format``   ``"json"`` (default: the event's JSON, unchanged), ``"avro"``, ``"protobuf"`` or
                   ``"json-schema"``: the schema is derived from the table's Arrow schema,
                   registered with the registry at ``schema_registry_url``, and the message value
                   is the Confluent wire format (see :mod:`shape_kafka.formats`).
``schema_registry_url``, ``subject_strategy`` (``topic``, ``record``, ``topic_record``),
``schema_registry_username`` and ``schema_registry_password``
                   the registry (``--sink-config kafka.KEY=VALUE``; the password is a credential
                   reference resolved before it reaches the emitter).
``config``         extra ``confluent-kafka`` producer settings (security, SASL, compression, ...);
                   ``bootstrap.servers`` comes from the URI. Defaults: ``acks=all``,
                   ``enable.idempotence=true``, ``linger.ms=5``.
``flush_timeout``  seconds to wait for acknowledgements (default 60).
``resuming``       accepted and ignored (a topic has no file to continue).

Rejections: a non-retryable error about one message (too large, an invalid record) or an event the
chosen ``event_format`` cannot hold does not fail the batch. Every other event is delivered and
acknowledged, then :class:`shape.streaming.emit.RejectedEvents` is raised with the keys and
reasons (``shape emit --dead-letter`` routes them). A dead-letter record (batch column
``_shape_dead_letter_reason``) also carries the header ``shape-dead-letter-reason``.

``confluent-kafka`` is imported when the first message is sent, never at plugin load.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from functools import partial
from typing import Any
from urllib.parse import unquote, urlsplit

import pyarrow as pa

from shape.errors import ShapeError
from shape.streaming.emit import RejectedEvents, Rejection
from shape.streaming.emit.formats import (
    ENVELOPES,
    FIELD_DEAD_REASON,
    FIELD_POISON,
    FIELD_SEQ,
    FIELD_TABLE,
    EncodedEvent,
    encode_events,
    poison_body,
)

from .formats import EVENT_FORMATS, EncodeError, TableCodec, make_codec, wire
from .registry import SUBJECT_STRATEGIES, RegistryClient, Transport, subject_name

DEFAULT_CONFIG: dict[str, Any] = {"acks": "all", "enable.idempotence": True, "linger.ms": 5}
HEADER_TABLE = "shape-table"
HEADER_SYNTHETIC = "shape-synthetic"
HEADER_DEAD_LETTER_REASON = "shape-dead-letter-reason"
# Errors about one message, not about the cluster or the topic: the broker or the client refuses
# this message for good. Such a message is a rejection (dead-lettered when there is a destination
# for them); every other failure is the destination's and stops the run after the retries.
PER_MESSAGE_ERRORS = frozenset(
    {
        "MSG_SIZE_TOO_LARGE",
        "_MSG_SIZE_TOO_LARGE",
        "INVALID_MSG",
        "INVALID_MSG_SIZE",
        "INVALID_RECORD",
        "CORRUPT_MESSAGE",
        "RECORD_LIST_TOO_LARGE",
    }
)
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


def _per_message_error(err: Any) -> str | None:
    """The name of ``err`` when it is a non-retryable error about one message, else ``None``."""
    name = getattr(err, "name", None)
    if not callable(name):
        return None
    retriable = getattr(err, "retriable", None)
    if callable(retriable) and retriable():
        return None
    text = str(name())
    return text if text in PER_MESSAGE_ERRORS else None


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
    event_formats = EVENT_FORMATS  # `--event-format`
    sink_config_keys = (  # `--sink-config kafka.KEY=VALUE`
        "schema_registry_url",
        "subject_strategy",
        "schema_registry_username",
        "schema_registry_password",
    )

    def __init__(
        self,
        producer_factory: Callable[[dict[str, Any]], Any] | None = None,
        registry_transport: Transport | None = None,
    ) -> None:
        self._factory = producer_factory or _confluent_producer
        self._registry_transport = registry_transport
        self._producers: dict[str, Any] = {}
        self._registries: dict[tuple[Any, ...], RegistryClient] = {}
        self._codecs: dict[tuple[Any, ...], tuple[TableCodec, int]] = {}

    def _registry(self, url: str, user: str | None, password: str | None) -> RegistryClient:
        key = (url, user, password)
        client = self._registries.get(key)
        if client is None:
            client = self._registries[key] = RegistryClient(
                url, username=user, password=password, transport=self._registry_transport
            )
        return client

    def _codec(
        self,
        fmt: str,
        topic: str,
        table: str,
        schema: pa.Schema,
        strategy: str,
        registry: RegistryClient,
    ) -> tuple[TableCodec, int]:
        """The codec of ``table`` and the id its schema got from the registry (registered on
        the table's first batch, once per run). A table whose columns change during the run (a
        drift plan that adds or drops one) gets a new schema, registered under the same subject:
        whether that is a compatible new version is the registry's decision."""
        shape = tuple((f.name, str(f.type), f.nullable) for f in schema if f.name != FIELD_POISON)
        key = (fmt, topic, strategy, table, shape)
        hit = self._codecs.get(key)
        if hit is None:
            codec = make_codec(fmt, table, schema)
            subject = subject_name(strategy, topic, codec.record)
            hit = self._codecs[key] = (
                codec,
                registry.register(subject, codec.schema_text, codec.schema_type),
            )
        return hit

    def _registry_events(
        self,
        batch: pa.RecordBatch,
        fmt: str,
        topic: str,
        strategy: str,
        registry: RegistryClient,
    ) -> tuple[list[EncodedEvent], list[Rejection]]:
        """``batch`` as wire-format events, in order, for ``fmt``; an event the format cannot
        hold is a :class:`Rejection` (the original flat JSON is its body) and is not in the
        list."""
        marks = (
            [bool(v) for v in batch.column(FIELD_POISON).to_pylist()]
            if FIELD_POISON in batch.schema.names
            else None
        )
        if marks is not None:
            batch = batch.select([n for n in batch.schema.names if n != FIELD_POISON])
        tables = batch.column(FIELD_TABLE).to_pylist()
        seqs = batch.column(FIELD_SEQ).to_pylist()
        times = (
            batch.column("_shape_event_time").cast(pa.string()).to_pylist()
            if "_shape_event_time" in batch.schema.names
            else [None] * len(tables)
        )
        bodies: list[bytes | EncodeError | None] = [None] * len(tables)
        for table in dict.fromkeys(tables):
            idx = [i for i, t in enumerate(tables) if t == table]
            part = batch if len(idx) == len(tables) else batch.take(pa.array(idx))
            codec, schema_id = self._codec(fmt, topic, table, part.schema, strategy, registry)
            for i, payload in zip(idx, codec.encode_batch(part), strict=True):
                bodies[i] = (
                    payload if isinstance(payload, EncodeError) else wire(schema_id, payload, fmt)
                )
        events: list[EncodedEvent] = []
        rejected: list[Rejection] = []
        for i, body in enumerate(bodies):
            key = f"{tables[i]}/{seqs[i]}"
            if isinstance(body, EncodeError):
                rejected.append(Rejection(key, f"cannot encode as {fmt}: {body}"))
                continue
            assert body is not None
            if marks is not None and marks[i]:
                body = poison_body(body)
            events.append(EncodedEvent(key, tables[i], seqs[i], times[i], body))
        return events, rejected

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
        event_format: str = "json",
        schema_registry_url: str | None = None,
        subject_strategy: str = "topic",
        schema_registry_username: str | None = None,
        schema_registry_password: str | None = None,
        **options: Any,
    ) -> int:
        """Send every batch; return the number of events, after the broker acknowledged them."""
        if options:
            raise ShapeError(f"unknown kafka emitter options: {sorted(options)}")
        if envelope not in ENVELOPES:
            raise ShapeError(f"unknown envelope {envelope!r}; choose from {', '.join(ENVELOPES)}")
        if event_format not in EVENT_FORMATS:
            raise ShapeError(
                f"unknown event format {event_format!r}; choose from {', '.join(EVENT_FORMATS)}"
            )
        registry: RegistryClient | None = None
        if event_format != "json":
            if envelope != "flat":
                raise ShapeError(
                    f"--envelope {envelope} is JSON only: it cannot be combined with "
                    f"--event-format {event_format}"
                )
            if subject_strategy not in SUBJECT_STRATEGIES:
                raise ShapeError(
                    f"unknown subject strategy {subject_strategy!r}; "
                    f"choose from {', '.join(SUBJECT_STRATEGIES)}"
                )
            if not schema_registry_url:
                raise ShapeError(
                    f"--event-format {event_format} needs a schema registry: "
                    "--sink-config kafka.schema_registry_url=URL"
                )
            registry = self._registry(
                schema_registry_url, schema_registry_username, schema_registry_password
            )
        servers, topic = parse_uri(uri)
        producer = self._producer(servers, config)
        failures: list[Any] = []
        rejected: list[Rejection] = []

        def reported(key: str, body: bytes, err: Any, _msg: Any = None) -> None:
            if err is None:
                return
            name = _per_message_error(err)
            if name is not None:
                rejected.append(Rejection(key, name, body))
            else:
                failures.append(err)

        sent = 0
        marker = [(HEADER_SYNTHETIC, b"true")] if synthetic else []
        for batch in batches:
            reasons = (
                batch.column(FIELD_DEAD_REASON).to_pylist()
                if FIELD_DEAD_REASON in batch.schema.names
                else None
            )
            if registry is None:
                events = encode_events(batch, envelope)
            else:
                events, refused = self._registry_events(
                    batch, event_format, topic, subject_strategy, registry
                )
                rejected.extend(refused)
            for i, event in enumerate(events):
                headers = [(HEADER_TABLE, event.table.encode("utf-8")), *marker]
                if reasons is not None:
                    headers.append((HEADER_DEAD_LETTER_REASON, str(reasons[i]).encode("utf-8")))
                while True:
                    try:
                        producer.produce(
                            topic,
                            value=event.body,
                            key=event.key.encode("utf-8"),
                            headers=headers,
                            on_delivery=partial(reported, event.key, event.body),
                        )
                        break
                    except BufferError:
                        # The local queue is full: serve delivery reports until it drains.
                        producer.poll(_POLL)
                    except Exception as exc:  # a KafkaException: the client refuses the message
                        name = _per_message_error(exc.args[0] if exc.args else None)
                        if name is None:
                            raise
                        rejected.append(Rejection(event.key, name, event.body))
                        break
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
        if rejected:
            raise RejectedEvents(rejected)
        return sent

    def flush(self) -> None:
        for producer in self._producers.values():
            producer.flush(60.0)

    def close(self) -> None:
        self.flush()
        self._producers.clear()
        self._codecs.clear()
        self._registries.clear()
