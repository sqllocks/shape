"""End to end against a real Kafka broker (the nightly job's container).

    docker compose -f ci/emulators/docker-compose.yml up -d --wait kafka
    pytest -m emulator plugins/shape-kafka/tests

``SHAPE_TEST_KAFKA`` overrides the bootstrap server (default ``localhost:9092``). Nothing here
is skipped when the broker is missing: the nightly job must fail if it cannot reach it.
"""

import json
import os
import time
import uuid
from datetime import timedelta

import pytest
from confluent_kafka import Producer
from confluent_kafka.admin import AdminClient, NewTopic
from shape_kafka import KafkaStreamSource

from shape.plugins import kit
from shape.streaming.checkpoint import FileCheckpointStore
from shape.streaming.consumer import StreamConsumer
from shape.streaming.messages import EVENT_TIME
from shape.streaming.runtime import GlobalProfiler, TumblingProfiler

pytestmark = pytest.mark.emulator

SERVERS = os.environ.get("SHAPE_TEST_KAFKA", "localhost:9092")
PARTITIONS = 3
ROWS = 1500
T0 = 1_700_000_000_000  # ms


def _rows():
    return [{"id": i, "amount": round(i * 0.25, 2), "kind": "abc"[i % 3]} for i in range(ROWS)]


@pytest.fixture(scope="module")
def topic():
    name = f"shape-e2e-{uuid.uuid4().hex[:8]}"
    admin = AdminClient({"bootstrap.servers": SERVERS})
    for fut in admin.create_topics([NewTopic(name, PARTITIONS, 1)]).values():
        fut.result(timeout=60)
    producer = Producer({"bootstrap.servers": SERVERS})
    for i, row in enumerate(_rows()):
        producer.produce(
            name,
            json.dumps(row).encode(),
            partition=i % PARTITIONS,
            timestamp=T0 + i * 10,
        )
    assert producer.flush(60) == 0
    yield name
    admin.delete_topics([name])


def uri(topic):
    return f"kafka://{SERVERS}/{topic}"


def test_kit_conformance(topic):
    kit.check_stream_source(KafkaStreamSource(), uri(topic))


def test_reads_every_message_once_with_per_partition_offsets(topic):
    src = KafkaStreamSource()
    out = list(src.read(uri(topic), batch_size=200))
    ids = [i for _, b in out for i in b.column("id").to_pylist()]
    assert sorted(ids) == list(range(ROWS))
    assert out[-1][0].value == {str(p): ROWS // PARTITIONS for p in range(PARTITIONS)}
    assert src.stats.messages == ROWS and src.stats.undecodable == 0
    first = out[0][1].column(EVENT_TIME).to_pylist()[0]
    assert first.timestamp() * 1000 >= T0  # the broker's message time


def test_resuming_from_every_offset_reads_the_rest(topic):
    src = KafkaStreamSource()
    out = list(src.read(uri(topic), batch_size=300))
    for k in range(len(out) - 1):
        rest = list(src.read(uri(topic), out[k][0], batch_size=300))
        got = sorted(i for _, b in rest for i in b.column("id").to_pylist())
        want = sorted(i for _, b in out[k + 1 :] for i in b.column("id").to_pylist())
        assert got == want


def test_follow_mode_sees_messages_produced_after_it_started(topic):
    name = f"{topic}-follow"
    admin = AdminClient({"bootstrap.servers": SERVERS})
    for fut in admin.create_topics([NewTopic(name, 1, 1)]).values():
        fut.result(timeout=60)
    try:
        producer = Producer({"bootstrap.servers": SERVERS})
        producer.produce(name, b'{"id": 0}', timestamp=T0)
        producer.flush(30)
        seen = []
        for k, (_, batch) in enumerate(
            KafkaStreamSource().read(
                uri(name), stop_at_end=False, idle_timeout=15, max_messages=3, batch_size=1
            )
        ):
            seen.extend(batch.column("id").to_pylist())
            if k == 0:  # produced while the read is running
                producer.produce(name, b'{"id": 1}', timestamp=T0 + 1)
                producer.produce(name, b'{"id": 2}', timestamp=T0 + 2)
                producer.flush(30)
        assert seen == [0, 1, 2]
    finally:
        admin.delete_topics([name])


def test_a_missing_topic_is_a_clear_error():
    from shape.streaming.messages import StreamSourceError

    with pytest.raises(StreamSourceError, match="does not exist"):
        list(KafkaStreamSource().read(f"kafka://{SERVERS}/no-such-topic-{uuid.uuid4().hex}"))


def test_an_unreachable_broker_is_a_connection_error_not_a_hang():
    started = time.monotonic()
    with pytest.raises(ConnectionError, match="kafka:"):
        list(KafkaStreamSource().read("kafka://127.0.0.1:1/t"))
    assert time.monotonic() - started < 60


class Crashing(KafkaStreamSource):
    """Dies (a non-transport error, as a killed process would) after ``crash_after`` batches."""

    def __init__(self, crash_after):
        super().__init__()
        self.crash_after = crash_after

    def read(self, uri, start=None, **options):
        for n, item in enumerate(super().read(uri, start, **options)):
            if n == self.crash_after:
                raise RuntimeError("killed")
            yield item


def _windows(consumer):
    return {
        (w.start, w.end): (w.rows, [c for c in w.profile["columns"] if c["name"] == "id"][0])
        for w in consumer.run()
    }


def test_a_killed_consumer_resumes_from_its_checkpoint_and_equals_an_uninterrupted_run(
    topic, tmp_path
):
    # Partitions are read independently, so event time is not ordered across them: the allowed
    # lateness covers the whole 15 s of the topic and every window closes at the end.
    def profiler(schema):
        return TumblingProfiler(
            schema, size=timedelta(seconds=1), allowed_lateness=timedelta(minutes=1)
        )

    schema = next(iter(KafkaStreamSource().read(uri(topic), batch_size=5)))[1].schema
    options = {"batch_size": 100, "schema": schema}
    done = _windows(
        StreamConsumer(KafkaStreamSource(), uri(topic), profiler(schema), options=options)
    )

    store = FileCheckpointStore(tmp_path / "ck.json")
    first = StreamConsumer(
        Crashing(6), uri(topic), profiler(schema), store, checkpoint_every=2, options=options
    )
    with pytest.raises(RuntimeError, match="killed"):
        list(first.run())
    assert store.load_document()["offset"] is not None  # a checkpoint was committed
    second = StreamConsumer(
        KafkaStreamSource(),
        uri(topic),
        profiler(schema),
        store,
        checkpoint_every=2,
        options=options,
    )
    got = _windows(second)
    assert got.keys() == done.keys() and len(got) == 15
    assert sum(rows for rows, _ in got.values()) == ROWS
    for key, (rows, col) in done.items():
        assert got[key][0] == rows
        for field in ("count", "min", "max", "finite_count"):
            assert got[key][1][field] == col[field]


def test_the_global_profile_of_the_topic_counts_every_row(topic):
    src = KafkaStreamSource()
    schema = next(iter(src.read(uri(topic), batch_size=5)))[1].schema
    (win,) = StreamConsumer(
        src, uri(topic), GlobalProfiler(schema), options={"batch_size": 250, "schema": schema}
    ).run()
    assert win.rows == ROWS


def test_stream_profile_on_a_real_broker(topic, tmp_path, capsys):
    """P3-05 end to end: the command, the real plugin, a real broker; the profile counts every
    message once, a followed windowed run is resumable, and a rerun of the finished one is a
    no-op."""
    from shape.cli.main import main

    out = tmp_path / "p.json"
    ck = tmp_path / "ck.json"
    argv = ["stream-profile", uri(topic), "-o", str(out), "--checkpoint", str(ck)]
    assert main([*argv, "--batch-size", "200"]) == 0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["events"] == ROWS and summary["undecodable"] == 0 and summary["rejected"] == 0
    doc = json.loads(out.read_text())
    cols = {c["name"]: c for c in doc["tables"]["stream"]["columns"]}
    assert doc["mode"] == "bounded" and doc["tables"]["stream"]["rows"] == ROWS
    assert cols["id"]["count"] == ROWS and cols["id"]["min"] == 0 and cols["id"]["max"] == ROWS - 1
    assert main(argv) == 0 and "finished run" in capsys.readouterr().out

    windows = tmp_path / "w.jsonl"
    code = main(
        [
            "stream-profile", uri(topic), "--window", "tumbling", "--size", "5s",
            "--allowed-lateness", "1m", "--windows", str(windows), "--batch-size", "250",
        ]
    )  # fmt: skip
    assert code == 0
    lines = [json.loads(x) for x in windows.read_text().splitlines()]
    assert sum(w["rows"] for w in lines) == ROWS and len(lines) == 3  # 15 s of event time
