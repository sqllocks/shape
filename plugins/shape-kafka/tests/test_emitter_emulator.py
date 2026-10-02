"""The Kafka emitter end to end against a real broker (the nightly job's container).

    docker compose -f ci/emulators/docker-compose.yml up -d --wait kafka
    pytest -m emulator plugins/shape-kafka/tests/test_emitter_emulator.py

``SHAPE_TEST_KAFKA`` overrides the bootstrap server (default ``localhost:9092``). Nothing here is
skipped when the broker is missing: the nightly job must fail if it cannot reach it.
"""

import json
import os
import time
import uuid

import pytest
from confluent_kafka import Consumer, Producer, TopicPartition
from confluent_kafka.admin import AdminClient, NewTopic
from shape_kafka import KafkaEmitter

from shape.errors import ShapeError
from shape.streaming.emit import EmitConfig, EmitRunner, EmitterSink, contract
from shape.streaming.emit.formats import FIELD_SEQ

pytestmark = pytest.mark.emulator

SERVERS = os.environ.get("SHAPE_TEST_KAFKA", "localhost:9092")
PARTITIONS = 3


class CountingProducer(Producer):
    """A real producer that counts the times its local queue was full."""

    full = 0

    def produce(self, *args, **kwargs):
        try:
            return super().produce(*args, **kwargs)
        except BufferError:
            CountingProducer.full += 1
            raise


def make_topic():
    name = f"shape-emit-{uuid.uuid4().hex[:8]}"
    admin = AdminClient({"bootstrap.servers": SERVERS})
    for fut in admin.create_topics([NewTopic(name, PARTITIONS, 1)]).values():
        fut.result(timeout=60)
    return name


def consume_all(topic):
    """``[(key, value)]`` of every message in the topic, partition by partition."""
    consumer = Consumer({"bootstrap.servers": SERVERS, "group.id": f"g-{uuid.uuid4().hex[:6]}"})
    try:
        tps = []
        end = {}
        for p in range(PARTITIONS):
            lo, hi = consumer.get_watermark_offsets(TopicPartition(topic, p), timeout=30)
            end[p] = hi
            tps.append(TopicPartition(topic, p, lo))
        consumer.assign(tps)
        out = {p: [] for p in range(PARTITIONS)}
        nxt = {p: tps[p].offset for p in range(PARTITIONS)}
        deadline = time.monotonic() + 60
        while any(nxt[p] < end[p] for p in nxt) and time.monotonic() < deadline:
            for m in consumer.consume(500, timeout=2):
                assert m.error() is None, m.error()
                out[m.partition()].append((m.key().decode(), m.value()))
                nxt[m.partition()] = m.offset() + 1
        return [kv for p in range(PARTITIONS) for kv in out[p]]
    finally:
        consumer.close()


class Harness:
    """The emitter contract's harness over a real topic. ``congest`` shrinks the producer's
    local queue so the real ``BufferError`` path runs; ``inject_failures`` raises before the
    next sends (a real broker cannot be told to fail)."""

    ordered = False  # three partitions: no total order

    def __init__(self):
        self.topic = make_topic()
        self.uri = f"kafka://{SERVERS}/{self.topic}"
        self.failures = 0
        self.queue = None

    def make(self):
        harness = self

        class Wrapped:
            inner = KafkaEmitter(lambda cfg: CountingProducer(cfg))

            def emit(self, uri, batches, **options):
                if harness.failures > 0:
                    harness.failures -= 1
                    raise ConnectionError("injected transient failure")
                if harness.queue:
                    options["config"] = {"queue.buffering.max.messages": harness.queue}
                return self.inner.emit(uri, batches, **options)

            def flush(self):
                self.inner.flush()

            def close(self):
                self.inner.close()

        return Wrapped()

    def delivered(self):
        return consume_all(self.topic)

    def inject_failures(self, n):
        self.failures = n

    def congest(self, n):
        CountingProducer.full = 0
        self.queue = 10

    def congestion_hits(self):
        return CountingProducer.full


def test_the_emitter_contract(tmp_path):
    contract.check_contract(Harness, directory=tmp_path)


def test_keyed_messages_keep_a_row_in_one_partition_across_replays():
    topic = make_topic()
    uri = f"kafka://{SERVERS}/{topic}"
    plan = contract.default_plan()
    batch = next(iter(plan.blocks(0))).batch.slice(0, 300)
    e = KafkaEmitter()
    e.emit(uri, [batch])
    e.emit(uri, [batch])  # a replay
    e.close()
    consumer = Consumer({"bootstrap.servers": SERVERS, "group.id": f"g-{uuid.uuid4().hex[:6]}"})
    try:
        where: dict[str, set[int]] = {}
        tps = [TopicPartition(topic, p, 0) for p in range(PARTITIONS)]
        consumer.assign(tps)
        seen = 0
        deadline = time.monotonic() + 60
        while seen < 600 and time.monotonic() < deadline:
            for m in consumer.consume(500, timeout=2):
                where.setdefault(m.key().decode(), set()).add(m.partition())
                seen += 1
        assert seen == 600 and len(where) == 300
        assert all(len(parts) == 1 for parts in where.values())
        assert len({next(iter(p)) for p in where.values()}) > 1  # spread over the partitions
    finally:
        consumer.close()


def test_a_run_with_a_tiny_queue_delivers_everything():
    topic = make_topic()
    sink = EmitterSink(KafkaEmitter(), f"kafka://{SERVERS}/{topic}")
    report = EmitRunner(
        contract.default_plan(),
        sink,
        EmitConfig(max_events=2000, batch_events=250, queue_batches=1),
    ).run()
    assert report.events == 2000
    got = {json.loads(v)[FIELD_SEQ] for _, v in consume_all(topic)}
    assert got == set(range(2000))


def test_an_unreachable_broker_is_a_timeout_not_a_hang():
    batch = next(iter(contract.default_plan().blocks(0))).batch.slice(0, 5)
    started = time.monotonic()
    with pytest.raises((TimeoutError, ConnectionError)):
        KafkaEmitter().emit(
            "kafka://127.0.0.1:1/t", [batch], flush_timeout=5, config={"message.timeout.ms": 3000}
        )
    assert time.monotonic() - started < 60


def test_a_missing_client_option_is_refused_before_anything_is_sent():
    with pytest.raises(ShapeError, match="unknown kafka emitter options"):
        KafkaEmitter().emit(f"kafka://{SERVERS}/t", [], nope=1)
