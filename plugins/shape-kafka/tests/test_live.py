"""The Kafka emitter against a real cluster (nightly, only where the secrets exist).

    KAFKA_SERVERS=host:9092 KAFKA_TOPIC=<topic, 1+ partitions> \\
    [KAFKA_CONFIG='{"security.protocol": "SASL_SSL", ...}'] \\
    pytest -m live plugins/shape-kafka/tests/test_live.py

A missing variable skips the live test with the missing setting named. Events
are read back from the topic's end as it was before the test.
"""

import json
import os
import time
import uuid

import pytest
from confluent_kafka import Consumer, TopicPartition
from shape_kafka import KafkaEmitter

from shape.streaming.emit import EmitConfig, EmitRunner, EmitterSink, contract
from shape.streaming.emit.formats import FIELD_SEQ

pytestmark = pytest.mark.live


def need(name):
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"missing live setting: {name}")
    return value


def test_a_run_is_delivered_with_the_idempotency_key():
    servers, topic = need("KAFKA_SERVERS"), need("KAFKA_TOPIC")
    extra = json.loads(os.environ.get("KAFKA_CONFIG", "{}"))
    consumer = Consumer(
        {**extra, "bootstrap.servers": servers, "group.id": f"shape-live-{uuid.uuid4().hex[:6]}"}
    )
    try:
        meta = consumer.list_topics(topic, timeout=30)
        parts = sorted(meta.topics[topic].partitions)
        start = {
            p: consumer.get_watermark_offsets(TopicPartition(topic, p), timeout=30)[1]
            for p in parts
        }
        sink = EmitterSink(KafkaEmitter(), f"kafka://{servers}/{topic}", config=extra)
        report = EmitRunner(
            contract.default_plan(), sink, EmitConfig(max_events=1000, batch_events=250)
        ).run()
        assert report.events == 1000
        consumer.assign([TopicPartition(topic, p, start[p]) for p in parts])
        seen = {}
        deadline = time.monotonic() + 120
        while len(seen) < 1000 and time.monotonic() < deadline:
            for m in consumer.consume(500, timeout=2):
                assert m.error() is None, m.error()
                seen[m.key().decode()] = json.loads(m.value())[FIELD_SEQ]
        assert seen == {f"order_line/{i}": i for i in range(1000)}
    finally:
        consumer.close()


def test_keyed_metadata_round_trip_live():
    servers, topic = need("KAFKA_SERVERS"), need("KAFKA_TOPIC")
    extra = json.loads(os.environ.get("KAFKA_CONFIG", "{}"))
    consumer = Consumer(
        {**extra, "bootstrap.servers": servers, "group.id": f"shape-keyed-{uuid.uuid4().hex}"}
    )
    try:
        start = consumer.get_watermark_offsets(TopicPartition(topic, 0), timeout=30)[1]
        batch = next(iter(contract.default_plan().blocks(0))).batch.slice(0, 3)
        emitter = KafkaEmitter()
        emitter.emit(
            f"kafka://{servers}/{topic}",
            [batch],
            key="_shape_table",
            partition=0,
            headers=["rehearsal=yes"],
            config=extra,
        )
        emitter.close()
        consumer.assign([TopicPartition(topic, 0, start)])
        messages = []
        deadline = time.monotonic() + 60
        while len(messages) < 3 and time.monotonic() < deadline:
            messages.extend(consumer.consume(3 - len(messages), timeout=2))
        assert len(messages) == 3
        assert [m.key() for m in messages] == [b"order_line"] * 3
        assert all(("rehearsal", b"yes") in m.headers() for m in messages)
        assert [dict(m.headers())["shape-key"] for m in messages] == [
            f"order_line/{i}".encode() for i in range(3)
        ]
    finally:
        consumer.close()
