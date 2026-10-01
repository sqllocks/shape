"""The kafka:// stream source against the in-memory broker (contract tests, every PR).

The real broker runs in ``test_emulator.py`` (nightly).
"""

import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pyarrow as pa
import pytest
from shape_kafka import KafkaStreamSource
from shape_kafka.source import parse_uri
from shape_kafka.testing import (
    FakeBroker,
    FakeError,
    FakeMessage,
    FakeTopicPartition,
    json_messages,
)

from shape.plugins import kit
from shape.plugins.api.v1 import StreamOffset
from shape.plugins.host import default_host
from shape.streaming.checkpoint import FileCheckpointStore
from shape.streaming.consumer import StreamConsumer
from shape.streaming.messages import EVENT_TIME, StreamSourceError
from shape.streaming.runtime import TumblingProfiler

pytestmark = pytest.mark.contract

URI = "kafka://broker-a:9092,broker-b:9092/orders"


def rows(n, start=0):
    return [{"id": i, "amount": i * 1.5, "kind": "abc"[i % 3]} for i in range(start, start + n)]


def broker(n=25, **kw):
    return FakeBroker({"orders": {0: json_messages(rows(n))}}, **kw)


def read(src, uri=URI, start=None, **options):
    return list(src.read(uri, start, **options))


def test_kit_conformance():
    kit.check_stream_source(broker(chunk=10).source(), URI)
    kit.check_stream_source(broker(chunk=7).source(), "kafka://localhost/orders")


def test_module_declares_the_api():
    import shape_kafka

    assert kit.check_module_api(shape_kafka) == "1.0"


def test_the_host_finds_the_plugin_by_its_entry_point():
    src = default_host().get("shape.stream_sources", "kafka")
    assert src.schemes == ("kafka",)
    assert src.can_open(URI)


def test_importing_the_plugin_does_not_import_the_client_library():
    code = "import sys, shape_kafka; assert 'confluent_kafka' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True)


def test_a_missing_client_library_is_a_clear_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "confluent_kafka", None)
    with pytest.raises(StreamSourceError, match="confluent-kafka"):
        read(KafkaStreamSource())


def test_uris():
    assert parse_uri(URI) == ("broker-a:9092,broker-b:9092", "orders")
    src = KafkaStreamSource()
    assert src.can_open("kafka://h/t")
    for bad in ("kafka://h", "kafka:///t", "kafka://h/a/b", "http://h/t", "/tmp/x.csv"):
        assert not src.can_open(bad)


def test_batches_carry_the_next_offset_of_every_partition():
    src = broker(chunk=10).source()
    out = read(src)
    assert [o.value for o, _ in out] == [{"0": 10}, {"0": 20}, {"0": 25}]
    assert [b.num_rows for _, b in out] == [10, 10, 5]
    assert all(isinstance(o, StreamOffset) for o, _ in out)
    names = out[0][1].schema.names
    assert names == ["id", "amount", "kind", EVENT_TIME]
    assert out[0][1].schema.field(EVENT_TIME).type == pa.timestamp("us", tz="UTC")
    assert src.stats.messages == 25 and src.stats.rows == 25


def test_the_event_time_is_the_payload_field_or_else_the_broker_time():
    ms = 1_700_000_000_000
    msgs = [
        (b'{"id": 1, "_shape_event_time": "2024-03-01T12:00:00Z"}', ms),
        (b'{"id": 2, "_shape_event_time": "2024-03-01T12:00:01"}', ms),  # no zone: UTC
        (b'{"id": 3, "_shape_event_time": "not a time"}', ms + 7),
        (b'{"id": 4}', ms + 9),
        (b'{"id": 5}', None),  # no broker time either
    ]
    b = FakeBroker({"orders": {0: msgs}})
    ((_, batch),) = read(b.source())
    got = batch.column(EVENT_TIME).to_pylist()
    assert got[0] == datetime(2024, 3, 1, 12, 0, tzinfo=UTC)
    assert got[1] == datetime(2024, 3, 1, 12, 0, 1, tzinfo=UTC)
    assert got[2] == datetime.fromtimestamp((ms + 7) / 1000, UTC)
    assert got[3] == datetime.fromtimestamp((ms + 9) / 1000, UTC)
    assert got[4] is None


def test_another_event_time_field_and_unit():
    msgs = [(b'{"id": 1, "ts": 1700000000}', None), (b'{"id": 2, "ts": 1700000002}', None)]
    b = FakeBroker({"orders": {0: msgs}})
    ((_, batch),) = read(b.source(), event_time_field="ts", event_time_unit="s")
    assert batch.schema.names == ["id", EVENT_TIME]
    times = batch.column(EVENT_TIME).to_pylist()
    assert times[1] - times[0] == timedelta(seconds=2)


def test_resuming_from_an_offset_reads_the_rest_and_nothing_twice():
    src = broker(chunk=10).source()
    everything = read(src)
    resumed = read(src, start=everything[0][0])
    assert [o.value for o, _ in resumed] == [{"0": 20}, {"0": 25}]
    both = pa.Table.from_batches([everything[0][1]] + [b for _, b in resumed])
    assert both.equals(pa.Table.from_batches([b for _, b in everything]))


def test_start_at_decides_where_new_partitions_begin():
    src = broker().source()
    assert sum(b.num_rows for _, b in read(src, start_at="earliest")) == 25
    assert read(src, start_at="latest") == []  # nothing arrives after the end it began at


def test_a_partition_missing_from_the_checkpoint_starts_at_start_at():
    b = FakeBroker({"orders": {0: json_messages(rows(6)), 1: json_messages(rows(4, 100))}})
    out = read(b.source(), start=StreamOffset({"0": 6}))
    assert sum(x.num_rows for _, x in out) == 4
    assert out[-1][0].value == {"0": 6, "1": 4}


def test_an_offset_below_what_the_broker_kept_starts_at_the_oldest_message():
    out = read(broker().source(), start=StreamOffset({"0": -5}))
    assert sum(x.num_rows for _, x in out) == 25
    assert read(broker().source(), start=StreamOffset({"0": 999})) == []  # past the end


def test_a_batch_never_mixes_partitions():
    b = FakeBroker({"orders": {p: json_messages(rows(5, 100 * p)) for p in (0, 1, 2)}})
    out = read(b.source())
    assert len(out) == 3
    for (_off, batch), p in zip(out, (0, 1, 2), strict=True):
        assert set(batch.column("id").to_pylist()) == set(range(100 * p, 100 * p + 5))
    assert out[-1][0].value == {"0": 5, "1": 5, "2": 5}


def test_max_messages_and_batch_size():
    out = read(broker().source(), max_messages=12, batch_size=5)
    assert [b.num_rows for _, b in out] == [5, 5, 2]
    assert out[-1][0].value == {"0": 12}


def test_follow_mode_stops_when_idle():
    src = broker().source()
    out = read(src, stop_at_end=False, idle_timeout=0.05)
    assert sum(b.num_rows for _, b in out) == 25


def test_poison_messages_are_counted_and_skipped_but_the_offset_moves_on():
    msgs = [(b'{"id": 1}', 1), (b"not json", 2), (b"[1, 2]", 3), (None, 4), (b'{"id": 2}', 5)]
    src = FakeBroker({"orders": {0: msgs}}).source()
    out = read(src)
    assert [b.column("id").to_pylist() for _, b in out] == [[1, 2]]
    assert out[-1][0].value == {"0": 5}
    assert src.stats.undecodable == 3 and src.stats.rows == 2
    with pytest.raises(StreamSourceError, match="offset 1"):
        read(FakeBroker({"orders": {0: msgs}}).source(), on_error="raise")


def test_the_schema_of_the_first_batch_holds_and_rows_that_do_not_fit_are_rejected():
    msgs = json_messages(
        [{"id": 1, "v": 1.5}, {"id": 2, "v": 2.5}, {"id": "x", "v": 3.5}, {"id": 4, "v": None}]
    )
    src = FakeBroker({"orders": {0: msgs}}, chunk=2).source()
    out = read(src)
    assert [b.schema for _, b in out][0] == [b.schema for _, b in out][1]
    assert out[1][1].column("id").to_pylist() == [4]  # "x" is not an integer
    assert src.stats.rejected == 1
    given = pa.schema(
        [("id", pa.int64()), ("v", pa.float64()), (EVENT_TIME, pa.timestamp("us", tz="UTC"))]
    )
    ((_, only),) = read(FakeBroker({"orders": {0: msgs[:2]}}).source(), schema=given)
    assert only.schema.equals(given)


def test_nested_values_become_json_text_and_mixed_columns_become_strings():
    msgs = json_messages([{"a": {"x": 1}, "b": 1}, {"a": [1, 2], "b": "two"}])
    ((_, batch),) = read(FakeBroker({"orders": {0: msgs}}).source())
    assert batch.column("a").to_pylist() == ['{"x":1}', "[1,2]"]
    assert batch.column("b").to_pylist() == ["1", "two"]


def test_with_offsets_adds_partition_and_offset_columns():
    ((_, batch),) = read(broker(n=3).source(), with_offsets=True)
    assert batch.schema.names[-2:] == ["_shape_partition", "_shape_offset"]
    assert batch.column("_shape_offset").to_pylist() == [0, 1, 2]
    assert batch.column("_shape_partition").to_pylist() == ["0", "0", "0"]


def test_a_transport_error_is_a_connection_error_and_another_error_is_not():
    src = broker(chunk=5, fail_at=5).source()
    with pytest.raises(ConnectionError, match="_TRANSPORT"):
        read(src)

    class Fatal:
        def name(self):
            return "_AUTHENTICATION"

        def __str__(self):
            return "bad credentials"

    b = broker()

    def broken(config):
        c = b.consumer(config)
        c.consume = lambda **_: [FakeMessage(0, 0, None, None, Fatal())]
        return c

    with pytest.raises(StreamSourceError, match="bad credentials"):
        read(KafkaStreamSource(broken, FakeTopicPartition))


def test_kafka_exceptions_from_the_client_are_mapped():
    class KafkaException(Exception):  # the client's exception type, by name
        pass

    class Unreachable(FakeError):
        def __str__(self):
            return "Failed to get metadata: Broker transport failure"

    def failing(error):
        b = broker()

        def make(config):
            c = b.consumer(config)

            def boom(topic, timeout=0):
                raise KafkaException(error)

            c.list_topics = boom
            return c

        return KafkaStreamSource(make, FakeTopicPartition)

    with pytest.raises(ConnectionError, match="(?i)broker transport failure"):
        read(failing(Unreachable("_TRANSPORT")))
    with pytest.raises(StreamSourceError, match="kafka:"):
        read(failing(FakeError("_AUTHENTICATION")))


def test_a_missing_topic_and_bad_options_are_errors():
    with pytest.raises(StreamSourceError, match="does not exist"):
        read(broker().source(), "kafka://h/nope")
    with pytest.raises(StreamSourceError, match="unknown kafka source options"):
        read(broker().source(), bogus=1)
    with pytest.raises(StreamSourceError, match="start_at"):
        read(broker().source(), start_at="yesterday")
    with pytest.raises(StreamSourceError, match="batch_size"):
        read(broker().source(), batch_size=0)


def test_the_consumer_is_assigned_explicit_offsets_and_commits_nothing():
    b = broker()
    read(b.source(), start=StreamOffset({"0": 10}), config={"security.protocol": "SASL_SSL"})
    (c,) = b.consumers
    assert c.config["bootstrap.servers"] == "broker-a:9092,broker-b:9092"
    assert c.config["enable.auto.commit"] is False
    assert c.config["security.protocol"] == "SASL_SSL"
    assert [tp.offset for tp in c.assigned] == [10]
    assert c.closed


def test_the_consumer_is_closed_when_the_reader_is_abandoned():
    b = broker(chunk=5)
    gen = b.source().read(URI)
    next(gen)
    gen.close()
    assert b.consumers[0].closed


def test_100_forced_reconnects_through_the_stream_consumer_equal_an_uninterrupted_run(tmp_path):
    data = rows(2400)

    def run(name, **faults):
        broker = FakeBroker({"orders": {0: json_messages(data, step_ms=5)}}, chunk=16, **faults)
        src = broker.source()
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
