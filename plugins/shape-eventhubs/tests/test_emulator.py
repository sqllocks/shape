"""End to end against the Azure Event Hubs emulator (the nightly job's containers).

    docker compose -f ci/emulators/docker-compose.yml up -d --wait azurite eventhubs
    pytest -m emulator plugins/shape-eventhubs/tests

The emulator (``ci/emulators/eventhubs/Config.json``) has the hub ``eh1`` with four partitions and
the consumer group ``cg1``. ``SHAPE_TEST_EVENTHUBS`` overrides the connection string. Nothing
here is skipped when the emulator is missing: the nightly job must fail if it cannot reach it.
The emulator keeps its events between runs of the same container, so the tests measure what they
send by sequence number, not by absolute count.
"""

import json
import os
import time
import uuid
from datetime import timedelta

import pytest
from azure.eventhub import EventData, EventHubConsumerClient, EventHubProducerClient
from shape_eventhubs import EventHubsStreamSource

from shape.plugins import kit
from shape.plugins.api.v1 import StreamOffset
from shape.streaming.checkpoint import FileCheckpointStore
from shape.streaming.consumer import StreamConsumer
from shape.streaming.messages import EVENT_TIME
from shape.streaming.runtime import TumblingProfiler

pytestmark = pytest.mark.emulator

CONNECTION = os.environ.get(
    "SHAPE_TEST_EVENTHUBS",
    "Endpoint=sb://localhost;SharedAccessKeyName=RootManageSharedAccessKey;"
    "SharedAccessKey=SAS_KEY_VALUE;UseDevelopmentEmulator=true;",
)
HUB = "eh1"
GROUP = "cg1"
URI = f"eventhubs://localhost/{HUB}?consumer_group={GROUP}"
OPTIONS = {"connection_string": CONNECTION}
ROWS = 1200


def _wait_until_ready():
    deadline = time.monotonic() + 120
    while True:
        try:
            client = EventHubConsumerClient.from_connection_string(
                CONNECTION, consumer_group=GROUP, eventhub_name=HUB
            )
            try:
                return client.get_partition_ids()
            finally:
                client.close()
        except Exception:  # the emulator is still starting
            if time.monotonic() > deadline:
                raise
            time.sleep(2)


def _ends():
    client = EventHubConsumerClient.from_connection_string(
        CONNECTION, consumer_group=GROUP, eventhub_name=HUB
    )
    try:
        out = {}
        for p in client.get_partition_ids():
            props = client.get_partition_properties(p)
            out[p] = 0 if props["is_empty"] else props["last_enqueued_sequence_number"] + 1
        return out
    finally:
        client.close()


class Run:
    """What this module sent: ``tag`` marks its events, ``start`` is where the hub ended before
    (the emulator keeps the events of earlier runs, which these tests must not read)."""

    def __init__(self, tag, start):
        self.tag = tag
        self.start = StreamOffset(start)


class FromStart(EventHubsStreamSource):
    """The source, reading from where this module's events begin (for the plugin kit)."""

    def __init__(self, start):
        super().__init__()
        self._start = start

    def read(self, uri, start=None, **options):
        return super().read(uri, start or self._start, **options)


@pytest.fixture(scope="module")
def run_id():
    partitions = _wait_until_ready()
    assert len(partitions) == 4
    tag = uuid.uuid4().hex[:8]
    run = Run(tag, _ends())
    producer = EventHubProducerClient.from_connection_string(CONNECTION, eventhub_name=HUB)
    try:
        for p in partitions:
            batch = producer.create_batch(partition_id=p)
            for i in range(int(p), ROWS, len(partitions)):
                body = json.dumps({"run": tag, "id": i, "kind": "abc"[i % 3]})
                try:
                    batch.add(EventData(body))
                except ValueError:
                    producer.send_batch(batch)
                    batch = producer.create_batch(partition_id=p)
                    batch.add(EventData(body))
            producer.send_batch(batch)
    finally:
        producer.close()
    return run


def _mine(out, run):
    return sorted(
        i
        for _, b in out
        for tag, i in zip(b.column("run").to_pylist(), b.column("id").to_pylist(), strict=True)
        if tag == run.tag
    )


def test_kit_conformance(run_id, monkeypatch):
    monkeypatch.setenv("SHAPE_EVENTHUBS_CONNECTION_STRING", CONNECTION)
    kit.check_stream_source(FromStart(run_id.start), URI)


def test_reads_every_event_once_with_per_partition_sequence_numbers(run_id):
    src = EventHubsStreamSource()
    out = list(src.read(URI, run_id.start, batch_size=100, **OPTIONS))
    assert _mine(out, run_id) == list(range(ROWS))
    last = out[-1][0].value
    assert set(last) == {"0", "1", "2", "3"}
    assert all(last[p] == run_id.start.value[p] + ROWS // 4 for p in last)
    assert src.stats.undecodable == 0
    assert EVENT_TIME in out[0][1].schema.names


def test_resuming_from_an_offset_reads_the_rest(run_id):
    src = EventHubsStreamSource()
    out = list(src.read(URI, run_id.start, batch_size=150, **OPTIONS))
    for k in (0, len(out) // 2, len(out) - 2):
        rest = list(src.read(URI, out[k][0], batch_size=150, **OPTIONS))
        assert _mine(rest, run_id) == _mine(out[k + 1 :], run_id)


def test_a_wrong_hub_is_an_error_not_a_hang():
    from shape.streaming.messages import StreamSourceError

    started = time.monotonic()
    with pytest.raises((StreamSourceError, ConnectionError)):
        list(
            EventHubsStreamSource().read(
                "eventhubs://localhost/no-such-hub-" + uuid.uuid4().hex[:6], **OPTIONS
            )
        )
    assert time.monotonic() - started < 120


class Crashing(FromStart):
    """Dies (a non-transport error, as a killed process would) after ``crash_after`` batches."""

    def __init__(self, start, crash_after):
        super().__init__(start)
        self.crash_after = crash_after

    def read(self, uri, start=None, **options):
        for n, item in enumerate(super().read(uri, start, **options)):
            if n == self.crash_after:
                raise RuntimeError("killed")
            yield item


def test_a_killed_consumer_resumes_from_its_checkpoint_and_loses_no_event(run_id, tmp_path):
    peek = FromStart(run_id.start).read(URI, batch_size=5, **OPTIONS)
    schema = next(peek)[1].schema
    peek.close()

    def consumer(source, store):
        prof = TumblingProfiler(
            schema, size=timedelta(hours=1), allowed_lateness=timedelta(days=3650)
        )  # partitions are not ordered against each other: every window closes at the end
        options = {"batch_size": 100, "schema": schema, **OPTIONS}
        return StreamConsumer(source, URI, prof, store, checkpoint_every=2, options=options)

    store = FileCheckpointStore(tmp_path / "ck.json")
    with pytest.raises(RuntimeError, match="killed"):
        list(consumer(Crashing(run_id.start, 4), store).run())
    assert store.load_document()["offset"] is not None  # a checkpoint was committed
    second = consumer(FromStart(run_id.start), store)
    assert sum(w.rows for w in second.run()) == ROWS
    assert second.duplicate_rows == 0


# Last: it adds events to the hub after this module's baseline, which the tests above count.
def test_follow_mode_sees_events_sent_after_it_started(run_id):
    producer = EventHubProducerClient.from_connection_string(CONNECTION, eventhub_name=HUB)
    tag = f"{run_id.tag}-late"
    try:
        seen = []
        reader = EventHubsStreamSource().read(
            URI,
            stop_at_end=False,
            start_at="latest",
            idle_timeout=30,
            max_messages=2,
            batch_size=1,
            **OPTIONS,
        )
        sender = None
        import threading

        def send():
            time.sleep(3)
            batch = producer.create_batch(partition_id="0")
            batch.add(EventData(json.dumps({"run": tag, "id": 1})))
            batch.add(EventData(json.dumps({"run": tag, "id": 2})))
            producer.send_batch(batch)

        sender = threading.Thread(target=send)
        sender.start()
        for _, b in reader:
            seen.extend(b.column("id").to_pylist())
        sender.join()
        assert sorted(seen) == [1, 2]
    finally:
        producer.close()


def test_stream_profile_on_the_emulator(run_id, tmp_path, capsys, monkeypatch):
    """P3-05 end to end: the command, the real plugin, the emulator. The emulator keeps the
    events of earlier runs, so this run's are counted by their ``run`` tag."""
    from shape.cli.main import main

    monkeypatch.setenv("SHAPE_EVENTHUBS_CONNECTION_STRING", CONNECTION)
    out = tmp_path / "p.json"
    assert main(["stream-profile", URI, "-o", str(out), "--batch-size", "500"]) == 0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["undecodable"] == 0 and summary["events"] >= ROWS
    doc = json.loads(out.read_text())
    cols = {c["name"]: c for c in doc["tables"]["stream"]["columns"]}
    assert doc["mode"] == "bounded" and cols["run"]["count"] == summary["events"]
    assert {t[0]: t[1] for t in cols["run"]["top"]}[run_id.tag] == ROWS
