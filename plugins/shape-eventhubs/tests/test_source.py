"""The eventhubs:// stream source against the in-memory hub (contract tests, every PR).

The real emulator runs in ``test_emulator.py`` (nightly).
"""

import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta

import pyarrow as pa
import pytest
from shape_eventhubs import EventHubsStreamSource
from shape_eventhubs.source import parse_uri
from shape_eventhubs.testing import FakeHub, json_events

from shape.plugins import kit
from shape.plugins.api.v1 import StreamOffset
from shape.plugins.host import default_host
from shape.streaming.checkpoint import FileCheckpointStore
from shape.streaming.consumer import StreamConsumer
from shape.streaming.messages import EVENT_TIME, StreamSourceError
from shape.streaming.runtime import TumblingProfiler

pytestmark = pytest.mark.contract

URI = "eventhubs://contoso.servicebus.windows.net/telemetry"


def rows(n, start=0):
    return [{"id": i, "amount": i * 1.5, "kind": "abc"[i % 3]} for i in range(start, start + n)]


def hub(n=25, **kw):
    return FakeHub({"0": json_events(rows(n))}, **kw)


def read(src, uri=URI, start=None, **options):
    return list(src.read(uri, start, **options))


def test_kit_conformance():
    kit.check_stream_source(hub(chunk=10).source(), URI)
    kit.check_stream_source(hub(chunk=7).source(), "eventhubs://localhost/eh1?consumer_group=cg1")


def test_module_declares_the_api():
    import shape_eventhubs

    assert kit.check_module_api(shape_eventhubs) == "1.0"


def test_the_host_finds_the_plugin_by_its_entry_point():
    src = default_host().get("shape.stream_sources", "eventhubs")
    assert src.schemes == ("eventhubs",)
    assert src.can_open(URI)


def test_importing_the_plugin_does_not_import_the_client_library():
    code = "import sys, shape_eventhubs; assert 'azure.eventhub' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True)


def test_a_missing_client_library_is_a_clear_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "azure.eventhub", None)
    with pytest.raises(StreamSourceError, match="azure-eventhub"):
        read(EventHubsStreamSource())


def test_without_credentials_the_error_says_what_to_give(monkeypatch):
    monkeypatch.delenv("SHAPE_EVENTHUBS_CONNECTION_STRING", raising=False)
    monkeypatch.setitem(sys.modules, "azure.identity", None)
    with pytest.raises(StreamSourceError, match="SHAPE_EVENTHUBS_CONNECTION_STRING"):
        read(EventHubsStreamSource())


def test_uris():
    assert parse_uri(URI) == ("contoso.servicebus.windows.net", "telemetry", "$Default")
    assert parse_uri("eventhubs://h/eh?consumer_group=cg1").consumer_group == "cg1"
    src = EventHubsStreamSource()
    assert src.can_open("eventhubs://h/t")
    for bad in ("eventhubs://h", "eventhubs:///t", "eventhubs://h/a/b", "kafka://h/t", "/x.csv"):
        assert not src.can_open(bad)


def test_batches_carry_the_next_sequence_number_of_every_partition():
    src = hub(chunk=10).source()
    out = read(src)
    assert [o.value for o, _ in out] == [{"0": 10}, {"0": 20}, {"0": 25}]
    assert [b.num_rows for _, b in out] == [10, 10, 5]
    assert all(isinstance(o, StreamOffset) for o, _ in out)
    assert out[0][1].schema.names == ["id", "amount", "kind", EVENT_TIME]
    assert out[0][1].schema.field(EVENT_TIME).type == pa.timestamp("us", tz="UTC")
    assert src.stats.messages == 25 and src.stats.rows == 25


def test_the_event_time_is_the_payload_field_or_else_the_enqueued_time():
    when = datetime(2024, 1, 2, 3, 4, 5, 678000, tzinfo=UTC)
    events = [
        ('{"id": 1, "_shape_event_time": "2024-03-01T12:00:00+02:00"}', when),
        ('{"id": 2}', when),
        ('{"id": 3}', None),
    ]
    ((_, batch),) = read(FakeHub({"0": events}).source())
    got = batch.column(EVENT_TIME).to_pylist()
    assert got == [datetime(2024, 3, 1, 10, 0, tzinfo=UTC), when, None]


def test_several_partitions_each_batch_from_one_partition():
    h = FakeHub({str(p): json_events(rows(5, 100 * p)) for p in range(3)}, chunk=5)
    out = read(h.source())
    assert len(out) == 3
    for (_off, batch), p in zip(
        sorted(out, key=lambda o: o[1].column("id")[0].as_py()), range(3), strict=True
    ):
        assert set(batch.column("id").to_pylist()) == set(range(100 * p, 100 * p + 5))
    assert out[-1][0].value == {"0": 5, "1": 5, "2": 5}


def test_resuming_from_an_offset_reads_the_rest_and_nothing_twice():
    src = hub(chunk=10).source()
    everything = read(src)
    resumed = read(src, start=everything[0][0])
    assert [o.value for o, _ in resumed] == [{"0": 20}, {"0": 25}]
    both = pa.Table.from_batches([everything[0][1]] + [b for _, b in resumed])
    assert both.equals(pa.Table.from_batches([b for _, b in everything]))


def test_start_at_decides_where_new_partitions_begin():
    src = hub().source()
    assert sum(b.num_rows for _, b in read(src, start_at="earliest")) == 25
    assert read(src, start_at="latest") == []


def test_a_partition_missing_from_the_checkpoint_starts_at_start_at():
    h = FakeHub({"0": json_events(rows(6)), "1": json_events(rows(4, 100))})
    out = read(h.source(), start=StreamOffset({"0": 6}))
    assert sum(x.num_rows for _, x in out) == 4
    assert out[-1][0].value == {"0": 6, "1": 4}


def test_an_empty_partition_and_an_offset_outside_what_the_hub_kept():
    h = FakeHub({"0": json_events(rows(5)), "1": []})
    out = read(h.source())
    assert sum(b.num_rows for _, b in out) == 5 and out[-1][0].value == {"0": 5, "1": 0}
    assert read(hub().source(), start=StreamOffset({"0": 999})) == []
    assert sum(b.num_rows for _, b in read(hub().source(), start=StreamOffset({"0": -3}))) == 25


def test_max_messages_and_batch_size():
    out = read(hub().source(), max_messages=12, batch_size=5)
    assert [b.num_rows for _, b in out] == [5, 5, 2]
    assert out[-1][0].value == {"0": 12}


def test_follow_mode_stops_when_idle():
    out = read(hub().source(), stop_at_end=False, idle_timeout=0.2)
    assert sum(b.num_rows for _, b in out) == 25


def test_poison_events_are_counted_and_skipped_but_the_position_moves_on():
    events = [('{"id": 1}', None), ("not json", None), ('"text"', None), ('{"id": 2}', None)]
    src = FakeHub({"0": events}).source()
    out = read(src)
    assert [b.column("id").to_pylist() for _, b in out] == [[1, 2]]
    assert out[-1][0].value == {"0": 4}
    assert src.stats.undecodable == 2
    with pytest.raises(StreamSourceError, match="offset 1"):
        read(FakeHub({"0": events}).source(), on_error="raise")


def test_the_schema_of_the_first_batch_holds_and_rows_that_do_not_fit_are_rejected():
    events = json_events([{"id": 1}, {"id": 2}, {"id": "x"}, {"id": 4}])
    src = FakeHub({"0": events}, chunk=2).source()
    out = read(src)
    assert out[0][1].schema == out[1][1].schema
    assert out[1][1].column("id").to_pylist() == [4]
    assert src.stats.rejected == 1


def test_a_connection_error_from_the_client_is_a_connection_error():
    with pytest.raises(ConnectionError, match="connection lost"):
        read(hub(chunk=5, fail_at=5).source())


def test_an_error_that_is_not_a_connection_problem_is_not_retried():
    h = hub()
    original = h.client

    def client(target, options):
        c = original(target, options)

        def receive_batch(on_event_batch, *, on_error, **_):
            on_error(None, ValueError("bad configuration"))

        c.receive_batch = receive_batch
        return c

    with pytest.raises(StreamSourceError, match="ValueError: bad configuration"):
        read(EventHubsStreamSource(client))


def test_events_delivered_twice_by_the_client_are_dropped_by_position():
    h = hub()
    original = h.client

    def client(target, options):
        c = original(target, options)

        def receive_batch(on_event_batch, *, starting_position, on_error=None, **_):
            from shape_eventhubs.testing import FakeContext

            events = h.partitions["0"]
            on_event_batch(FakeContext("0"), events[0:10])
            on_event_batch(FakeContext("0"), events[5:15])  # 5..9 again
            on_event_batch(FakeContext("0"), events[15:])
            while not c.closed.is_set():
                time.sleep(0.01)

        c.receive_batch = receive_batch
        return c

    out = read(EventHubsStreamSource(client))
    ids = [i for _, b in out for i in b.column("id").to_pylist()]
    assert ids == list(range(25))


def test_the_client_is_closed_and_the_worker_thread_ends_when_the_reader_is_abandoned():
    h = hub(chunk=5)
    gen = h.source().read(URI)
    next(gen)
    gen.close()
    assert all(c.closed.is_set() for c in h.clients)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and any(
        t.name == "shape-eventhubs-receive" for t in threading.enumerate()
    ):
        time.sleep(0.05)
    assert not any(t.name == "shape-eventhubs-receive" for t in threading.enumerate())


def test_a_missing_hub_and_bad_options_are_errors():
    with pytest.raises(StreamSourceError, match="unknown eventhubs source options"):
        read(hub().source(), bogus=1)
    with pytest.raises(StreamSourceError, match="start_at"):
        read(hub().source(), start_at="yesterday")
    with pytest.raises(StreamSourceError, match="no partitions"):
        read(FakeHub({}).source())


def test_100_forced_reconnects_through_the_stream_consumer_equal_an_uninterrupted_run(tmp_path):
    data = rows(2400)

    def run(name, **faults):
        h = FakeHub({"0": json_events(data, step=timedelta(milliseconds=5))}, chunk=16, **faults)
        src = h.source()
        schema = next(iter(src.read(URI, batch_size=1)))[1].schema
        prof = TumblingProfiler(schema, size=timedelta(milliseconds=500), allowed_lateness=0)
        consumer = StreamConsumer(
            src,
            URI,
            prof,
            FileCheckpointStore(tmp_path / f"{name}.json"),
            checkpoint_every=3,
            options={"batch_size": 16, "schema": schema},
            max_attempts=3,
        )
        return consumer, {(w.start, w.end): w for w in consumer.run()}

    clean, expected = run("clean")
    flaky, got = run("flaky", fail_at=16, fail_every=16)
    assert clean.reconnects == 0 and flaky.reconnects >= 100
    assert got.keys() == expected.keys()
    assert sum(w.rows for w in got.values()) == 2400
    for key, win in expected.items():
        assert got[key].rows == win.rows and got[key].profile == win.profile
