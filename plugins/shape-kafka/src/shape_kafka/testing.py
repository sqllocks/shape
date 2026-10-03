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


class FakeKafkaException(Exception):  # noqa: N818 - named like confluent_kafka.KafkaException
    """What ``produce`` raises for a message the client refuses: ``args[0]`` is the error."""


class FakeProducer:
    """The slice of ``confluent_kafka.Producer`` the emitter uses, over an in-memory log.

    ``produce`` queues a message; ``flush`` delivers the queue and calls each ``on_delivery``.
    ``full`` makes ``produce`` raise ``BufferError`` until ``poll`` has been called that many
    times (a full local queue draining); ``failures`` makes the next flushes fail: the first half
    of the queued messages is delivered and the rest reports a delivery error, as a broker that
    drops mid-batch does, so the batch is delivered twice in part when it is retried.
    """

    def __init__(self, store: FakeProducerStore, config: Mapping[str, Any]) -> None:
        self.store = store
        self.config = dict(config)
        self.queue: list[tuple[str, bytes | None, bytes | None, Any, Any]] = []

    def produce(
        self,
        topic: str,
        value: bytes | None = None,
        key: bytes | None = None,
        headers: Any = None,
        on_delivery: Any = None,
        **_: Any,
    ) -> None:
        if self.store.full > 0:
            self.store.hits += 1
            raise BufferError("Local: Queue full")
        if self.store.refuse_at_produce > 0:
            self.store.refuse_at_produce -= 1
            raise FakeKafkaException(FakeError("_MSG_SIZE_TOO_LARGE"))
        self.queue.append((topic, key, value, headers, on_delivery))

    def poll(self, timeout: float = 0) -> int:
        if self.store.full > 0:
            self.store.full -= 1
        return 0

    def flush(self, timeout: float = -1) -> int:
        queue, self.queue = self.queue, []
        fail = self.store.failures > 0
        if fail:
            self.store.failures -= 1
        cut = len(queue) // 2 if fail else len(queue)
        for i, (topic, key, value, headers, cb) in enumerate(queue):
            if self.store.reject > 0 and i < cut:
                self.store.reject -= 1
                self.store.rejected.append(key.decode() if key else "")
                if cb is not None:
                    cb(FakeError("MSG_SIZE_TOO_LARGE"), None)
            elif i < cut:
                self.store.log.append((topic, key, value, headers))
                if cb is not None:
                    cb(None, None)
            elif cb is not None:
                cb(FakeError("_MSG_TIMED_OUT", retriable=True), None)
        return 0


class FakeProducerStore:
    """What the producers of one fake cluster sent, and the faults to inject."""

    def __init__(self) -> None:
        self.log: list[tuple[str, bytes | None, bytes | None, Any]] = []
        self.full = 0
        self.failures = 0
        self.hits = 0
        self.reject = 0  # the next messages the broker refuses for good (MSG_SIZE_TOO_LARGE)
        self.refuse_at_produce = 0  # the next produce() calls the client itself refuses
        self.rejected: list[str] = []  # keys refused
        self.producers: list[FakeProducer] = []

    def factory(self, config: Mapping[str, Any]) -> FakeProducer:
        p = FakeProducer(self, config)
        self.producers.append(p)
        return p


class EmitterHarness:
    """The emitter contract's harness (``shape.streaming.emit.contract``) over a fake cluster."""

    def __init__(self, topic: str = "events", registry: FakeRegistry | None = None) -> None:
        self.store = FakeProducerStore()
        self.registry = registry or FakeRegistry()
        self.uri = f"kafka://broker-a:9092,broker-b:9092/{topic}"

    def make(self) -> Any:
        from .emitter import KafkaEmitter

        return KafkaEmitter(self.store.factory, registry_transport=self.registry)

    def delivered(self) -> list[tuple[str, bytes]]:
        return [(k.decode() if k else "", v or b"") for _, k, v, _ in self.store.log]

    def inject_failures(self, n: int) -> None:
        self.store.failures = n

    def congest(self, n: int) -> None:
        self.store.full = n

    def congestion_hits(self) -> int:
        return self.store.hits

    def inject_rejections(self, n: int) -> None:
        """The next ``n`` messages delivered are refused for good (a per-message error)."""
        self.store.reject = n


# ---- schema registry fake and decoders (formats tests) ----------------------------------------


class FakeRegistry:
    """An in-process Confluent-compatible schema registry; an instance is a
    :class:`~shape_kafka.registry.Transport` (no network).

    ``POST /subjects/{subject}/versions`` registers a schema (the same schema under the same
    subject gives the same id; a different one is refused with 409, as a registry in a
    compatibility mode that forbids the change does), ``GET /schemas/ids/{id}`` reads it back.
    ``evolve`` accepts a changed schema as a new version of the subject.
    ``refuse`` maps a subject to the message of a 422 refusal; ``auth`` is ``(user, password)``
    that every request must present as basic authentication (401 otherwise); ``down`` makes
    every request fail with 503.
    """

    def __init__(
        self,
        *,
        refuse: Mapping[str, str] | None = None,
        auth: tuple[str, str] | None = None,
        down: bool = False,
        evolve: bool = False,
    ) -> None:
        self.evolve = evolve  # a changed schema is a new version, not a 409
        self.refuse = dict(refuse or {})
        self.auth = auth
        self.down = down
        self.schemas: dict[int, tuple[str, str]] = {}  # id -> (schema text, schema type)
        self.subjects: dict[str, tuple[int, str]] = {}  # subject -> (id, schema text)
        self.requests: list[tuple[str, str, dict[str, Any] | None, dict[str, str]]] = []
        self._next = 100

    def __call__(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        from urllib.parse import unquote, urlsplit

        doc = json.loads(body) if body else None
        self.requests.append((method, url, doc, dict(headers)))
        if self.down:
            return 503, b'{"error_code":50301,"message":"unavailable"}'
        if self.auth is not None:
            import base64

            want = "Basic " + base64.b64encode(f"{self.auth[0]}:{self.auth[1]}".encode()).decode()
            if headers.get("Authorization") != want:
                return 401, b'{"error_code":40101,"message":"Unauthorized"}'
        parts = [unquote(p) for p in urlsplit(url).path.split("/") if p]
        if (
            method == "POST"
            and len(parts) == 3
            and parts[0] == "subjects"
            and parts[2] == "versions"
        ):
            subject = parts[1]
            if subject in self.refuse:
                return 422, json.dumps(
                    {"error_code": 42201, "message": self.refuse[subject]}
                ).encode()
            assert doc is not None
            text, kind = doc["schema"], doc.get("schemaType", "AVRO")
            known = self.subjects.get(subject)
            if known is not None and known[1] != text and self.evolve:
                known = None  # a new version of the subject
            if known is not None:
                if known[1] == text:
                    return 200, json.dumps({"id": known[0]}).encode()
                message = (
                    "Schema being registered is incompatible with an earlier schema "
                    f'for subject "{subject}"'
                )
                return 409, json.dumps({"error_code": 409, "message": message}).encode()
            for sid, (t, k) in self.schemas.items():
                if t == text and k == kind:
                    self.subjects[subject] = (sid, text)
                    return 200, json.dumps({"id": sid}).encode()
            sid, self._next = self._next, self._next + 1
            self.schemas[sid] = (text, kind)
            self.subjects[subject] = (sid, text)
            return 200, json.dumps({"id": sid}).encode()
        if method == "GET" and len(parts) == 3 and parts[:2] == ["schemas", "ids"]:
            sid = int(parts[2])
            if sid not in self.schemas:
                return 404, b'{"error_code":40403,"message":"Schema not found"}'
            text, kind = self.schemas[sid]
            return 200, json.dumps({"schema": text, "schemaType": kind}).encode()
        return 404, b'{"error_code":404,"message":"not found"}'


def _arrow_from_decoded(values: Sequence[Any], t: Any, source: str) -> Any:
    """``values`` as read back from a message, as an Arrow array of the original type ``t``."""
    import uuid

    import pyarrow as pa

    if pa.types.is_dictionary(t):
        return _arrow_from_decoded(values, t.value_type, source).dictionary_encode()
    if isinstance(t, pa.BaseExtensionType) and t.extension_name == "arrow.uuid":
        return pa.array([None if v is None else uuid.UUID(str(v)) for v in values], t)
    if source == "protobuf":
        if pa.types.is_date(t):
            return pa.array(values, pa.int32()).cast(pa.date32()).cast(t)
        if pa.types.is_time(t):
            return pa.array(values, pa.int64()).cast(pa.time64("us")).cast(t, safe=False)
        if pa.types.is_timestamp(t):
            return pa.array(values, pa.int64()).cast(pa.timestamp("us", t.tz)).cast(t, safe=False)
        if pa.types.is_decimal(t):
            return pa.array(values, pa.string()).cast(t)
    if source == "avro" and pa.types.is_timestamp(t) and t.unit == "ns":
        return pa.array(values, pa.timestamp("us", t.tz)).cast(t)
    if source == "avro" and pa.types.is_time(t) and t.unit == "ns":
        return pa.array(values, pa.time64("us")).cast(t)
    if pa.types.is_binary(t) or pa.types.is_large_binary(t) or pa.types.is_fixed_size_binary(t):
        return pa.array(values, pa.binary()).cast(t)
    return pa.array(values, t)


_PROTO_FIELD = {
    "bool": 8,
    "int32": 5,
    "int64": 3,
    "uint32": 13,
    "uint64": 4,
    "float": 2,
    "double": 1,
    "string": 9,
    "bytes": 12,
}


def _proto_decoder(text: str) -> Any:
    """A message class built from the registered ``.proto`` text (the subset Shape writes), so a
    decode depends on the registry's copy of the schema and nothing the encoder holds."""
    import re

    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

    package = re.search(r"^package ([\w.]+);", text, re.M)
    name = re.search(r"^message (\w+) \{", text, re.M)
    assert package and name
    fdp = descriptor_pb2.FileDescriptorProto(
        name=f"{name.group(1)}.proto", package=package.group(1), syntax="proto3"
    )
    msg = fdp.message_type.add(name=name.group(1))
    for m in re.finditer(r"^\s+(optional )?(\w+) (\w+) = (\d+);", text, re.M):
        f = msg.field.add(
            name=m.group(3),
            number=int(m.group(4)),
            type=_PROTO_FIELD[m.group(2)],
            label=descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL,
        )
        if m.group(1):
            f.proto3_optional = True
            f.oneof_index = len(msg.oneof_decl)
            msg.oneof_decl.add(name=f"_{m.group(3)}")
    pool = descriptor_pool.DescriptorPool()
    pool.Add(fdp)
    return message_factory.GetMessageClass(
        pool.FindMessageTypeByName(f"{package.group(1)}.{name.group(1)}")
    )


def decode_messages(
    registry: FakeRegistry, values: Sequence[bytes], schema: Any
) -> list[dict[str, Any]]:
    """Read Confluent wire-format messages (Avro, Protobuf, JSON Schema) back to the flat events
    they stand for: the schema comes from ``registry`` by the id in each message, the values are
    rebuilt as Arrow columns of the original ``schema`` (the table's event schema) and written
    with the runtime's own JSON rules. Equal to the flat events means equal value for value under
    the documented type mapping."""
    import io
    import struct

    import pyarrow as pa

    from shape.streaming.emit.formats import FIELD_POISON, rows_of

    names = [n for n in schema.names if n != FIELD_POISON]
    columns: dict[str, list[Any]] = {n: [] for n in names}
    sources: set[str] = set()
    for value in values:
        if value[0] != 0:
            raise ValueError("not the Confluent wire format: the first byte is not 0")
        (sid,) = struct.unpack(">I", value[1:5])
        text, kind = registry.schemas[sid]
        payload = value[5:]
        if kind == "AVRO":
            import fastavro

            parsed = fastavro.parse_schema(json.loads(text))
            record = fastavro.schemaless_reader(io.BytesIO(payload), parsed)
            sources.add("avro")
            for n in names:
                columns[n].append(record[n])
        elif kind == "PROTOBUF":
            if payload[:1] != b"\x00":
                raise ValueError("a Protobuf message starts with the message index list [0]")
            m = _proto_decoder(text)()
            m.ParseFromString(payload[1:])
            sources.add("protobuf")
            fields = {f.name: f for f in m.DESCRIPTOR.fields}
            for n in names:
                present = not fields[n].has_presence or m.HasField(n)
                columns[n].append(getattr(m, n) if present else None)
        else:
            event = json.loads(payload)
            sources.add("json-schema")
            for n in names:
                columns[n].append(event[n])
    if not values:
        return []
    (source,) = sources
    if source == "json-schema":  # the payload is the flat event already
        return [dict(zip(names, row, strict=True)) for row in zip(*columns.values(), strict=True)]
    arrays = [_arrow_from_decoded(columns[n], schema.field(n).type, source) for n in names]
    return rows_of(pa.RecordBatch.from_arrays(arrays, names=names))
