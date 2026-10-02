"""The Kafka emitter against a real cluster (nightly, only where the secrets exist).

    KAFKA_SERVERS=host:9092 KAFKA_TOPIC=<topic, 1+ partitions> \\
    [KAFKA_CONFIG='{"security.protocol": "SASL_SSL", ...}'] \\
    pytest -m live plugins/shape-kafka/tests/test_live.py

A missing variable fails the test with the variable's name (nothing is silently skipped). Events
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
    assert value, f"{name} is not set (live tests need it)"
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
