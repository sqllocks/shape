"""P3-03: offset-committed checkpoints, reconnects that resume, deduplication on offset."""

from __future__ import annotations

import json
import math
import os
from datetime import timedelta

import numpy as np
import pyarrow as pa
import pytest

from shape.connectors.qualification import reconnecting_batches
from shape.plugins.api.v1 import StreamOffset
from shape.streaming.checkpoint import CheckpointError, FileCheckpointStore
from shape.streaming.consumer import StreamConsumer
from shape.streaming.runtime import GlobalProfiler, SlidingProfiler, TumblingProfiler

SEC = 1_000_000
SCHEMA = pa.schema(
    [
        ("_shape_event_time", pa.timestamp("us")),
        ("partition", pa.string()),
        ("offset", pa.int64()),
        ("amount", pa.float64()),
        ("cat", pa.string()),
    ]
)


def _approx(a, b, path="$"):
    if isinstance(a, float) or isinstance(b, float):
        if a is None or b is None:
            assert a is b, path
        elif math.isnan(a) or math.isnan(b):
            assert math.isnan(a) and math.isnan(b), path
        else:
            assert a == pytest.approx(b, rel=1e-9, abs=1e-9), (path, a, b)
    elif isinstance(a, dict):
        assert set(a) == set(b), path
        for k in a:
            _approx(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, (list, tuple)):
        assert len(a) == len(b), path
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            _approx(x, y, f"{path}[{i}]")
    else:
        assert a == b, (path, a, b)


# ---------------------------------------------------------------- a source


def make_log(partitions: int = 3, per_partition: int = 400, seed: int = 0):
    """The records of a partitioned log, in the global order the source delivers them: rows are
    ``(event time, partition, offset, amount, cat)`` with event times that never go backwards."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(partitions * per_partition):
        p = i % partitions
        rows.append((i // 2, str(p), i // partitions, round(float(rng.normal(50, 9)), 3)))
    cats = rng.choice(["a", "b", "c", "dd"], len(rows)).tolist()
    return [(*r, c) for r, c in zip(rows, cats, strict=True)]


def _batch(rows) -> pa.RecordBatch:
    cols = list(zip(*rows, strict=True)) if rows else [[]] * 5
    return pa.record_batch(
        [
            pa.array([int(t) * SEC for t in cols[0]], pa.timestamp("us")),
            pa.array(list(cols[1]), pa.string()),
            pa.array(list(cols[2]), pa.int64()),
            pa.array(list(cols[3]), pa.float64()),
            pa.array(list(cols[4]), pa.string()),
        ],
        schema=SCHEMA,
    )


class LogSource:
    """A ``StreamSource`` over a partitioned log that fails and replays.

    * delivers ``(StreamOffset, batch)`` with the offset after the batch (``{partition: next}``);
    * ``fail_every``: raises ``ConnectionError`` after that many batches, ``failures`` times;
    * ``rewind``: on every connection after a failure it starts up to that many batches *before*
      the offset it was asked to resume from (at-least-once delivery), cutting the batches
      differently each time.
    """

    def __init__(self, log, batch_rows=37, fail_every=0, failures=0, rewind=0, seed=1):
        self.log, self.batch_rows = log, batch_rows
        self.fail_every, self.failures, self.rewind = fail_every, failures, rewind
        self.rng = np.random.default_rng(seed)
        self.connections = 0
        self.failed = 0

    def read(self, uri, start=None, **options):
        self.connections += 1
        positions = {} if start is None else dict(start.value)
        # the log index to resume from: after the rows the offset has covered
        index = 0
        while index < len(self.log) and int(self.log[index][2]) < positions.get(
            self.log[index][1], 0
        ):
            index += 1
        if self.connections > 1 and self.rewind:
            index = max(0, index - int(self.rng.integers(0, self.rewind + 1)))
        delivered = 0
        size = self.batch_rows if self.connections == 1 else int(self.rng.integers(5, 60))
        covered = {p: 0 for p in {r[1] for r in self.log}}
        for r in self.log[:index]:
            covered[r[1]] = max(covered[r[1]], int(r[2]) + 1)
        while index < len(self.log):
            if self.fail_every and self.failed < self.failures and delivered >= self.fail_every:
                self.failed += 1
                raise ConnectionError("forced reconnect")
            chunk = self.log[index : index + size]
            index += len(chunk)
            for r in chunk:
                covered[r[1]] = max(covered[r[1]], int(r[2]) + 1)
            delivered += 1
            yield StreamOffset(dict(covered)), _batch(chunk)


def _tumbling(**kw):
    return TumblingProfiler(SCHEMA, timedelta(seconds=40), **kw)


def _consume(source, profiler, store=None, **kw):
    kw.setdefault("offset_column", "offset")
    kw.setdefault("partition_column", "partition")
    c = StreamConsumer(source, "log://t", profiler, store, **kw)
    return c, [w.to_dict() for w in c.run()]


# ----------------------------------------------------------------------- S4


def test_s4_reconnect_does_not_replay_what_was_already_yielded():
    """S4: ``reconnecting_batches`` reconnected with ``connect()``, which restarts the stream, so
    every batch yielded before the failure was yielded again."""
    calls = [0]

    def connect():
        calls[0] += 1
        for i in range(1, 6):
            if calls[0] == 1 and i == 3:
                raise OSError("transport")
            if calls[0] == 2 and i == 5:
                raise OSError("transport")
            yield i

    assert list(reconnecting_batches(connect, 5)) == [1, 2, 3, 4, 5]
    assert calls[0] == 3


def test_reconnect_resumes_from_the_offset_after_the_last_batch():
    starts = []

    def connect(start):
        starts.append(start)
        first = 0 if start is None else start + 1
        for i in range(first, 10):
            if len(starts) <= 3 and i == first + 2:
                raise ConnectionError("drop")
            yield i, f"batch {i}"

    got = list(reconnecting_batches(connect, 2))
    assert [g[0] for g in got] == list(range(10))  # each batch once, in order
    assert starts == [None, 1, 3, 5]
    with pytest.raises(TypeError, match="must yield"):
        list(reconnecting_batches(lambda s: iter([1, 2]), 2))


def test_the_attempt_bound_counts_consecutive_failures():
    def always_down(start):
        raise ConnectionError("down")
        yield  # pragma: no cover

    with pytest.raises(ConnectionError):
        list(reconnecting_batches(always_down, 3))

    # failures that each follow progress never reach the bound
    def flaky(start):
        first = 0 if start is None else start + 1
        if first >= 50:
            raise ConnectionError("down for good")
        yield first, first
        raise ConnectionError("drop")

    out = []
    with pytest.raises(ConnectionError, match="for good"):
        for item in reconnecting_batches(flaky, 3, backoff=0):  # 50 drops: no pauses
            out.append(item[0])
    assert out == list(range(50))  # 50 failures survived, then 3 in a row stopped it


# --------------------------------------------------- acceptance: reconnects


@pytest.mark.parametrize("make", [_tumbling, lambda **kw: GlobalProfiler(SCHEMA)])
def test_across_100_forced_reconnects_the_output_equals_an_uninterrupted_run(make):
    """Acceptance (P3-03): 100 forced reconnects, with at-least-once redelivery and different
    batch cuts on every reconnection; after deduplication on offset the windows are those of an
    uninterrupted run."""
    log = make_log(partitions=3, per_partition=4000, seed=4)
    clean = _consume(LogSource(log, batch_rows=40), make())
    # 3 batches of at least 5 rows per connection, and a rewind of at most 10 rows: every
    # connection delivers something new, and there are more than 100 of them
    flaky_source = LogSource(log, batch_rows=40, fail_every=3, failures=100, rewind=10, seed=9)
    seen = []
    c, flaky = _consume(
        flaky_source,
        make(),
        max_attempts=3,
        on_reconnect=lambda n, exc: seen.append(exc),
    )
    assert flaky_source.failed == 100 and c.reconnects == 100 and len(seen) == 100
    assert c.duplicate_rows > 0  # the source really replayed rows, and they were dropped
    assert c.profiler.rows_in == len(log)  # every row reached the profiler exactly once
    assert sum(w["rows"] for w in flaky) == len(log) == sum(w["rows"] for w in clean[1])
    assert len(flaky) == len(clean[1])
    for got, want in zip(flaky, clean[1], strict=True):
        assert (got["start_us"], got["end_us"], got["rows"]) == (
            want["start_us"],
            want["end_us"],
            want["rows"],
        )
        _approx(got["profile"], want["profile"])


def test_reconnects_resume_from_the_offset_map_when_there_is_no_offset_column():
    log = make_log(partitions=2, per_partition=3000, seed=2)
    kw = {"offset_column": None, "partition_column": None}
    clean = _consume(LogSource(log), GlobalProfiler(SCHEMA), **kw)
    source = LogSource(log, fail_every=3, failures=40, rewind=0, seed=3)
    c, flaky = _consume(source, GlobalProfiler(SCHEMA), max_attempts=3, **kw)
    assert source.failed == 40 and flaky[0]["rows"] == len(log) and c.duplicate_rows == 0
    _approx(flaky[0]["profile"], clean[1][0]["profile"])


def test_whole_batches_delivered_twice_are_dropped_by_the_offset_map():
    b1, b2 = _batch([(1, "0", i, 1.0, "x") for i in range(3)]), _batch([(2, "0", 3, 2.0, "y")])

    class Twice:
        def read(self, uri, start=None, **options):
            yield StreamOffset({"0": 3}), b1
            yield StreamOffset({"0": 3}), b1
            yield StreamOffset({"0": 4}), b2
            yield StreamOffset({"0": 3}), b1

    c, out = _consume(Twice(), GlobalProfiler(SCHEMA), offset_column=None, partition_column=None)
    assert out[0]["rows"] == 4 and c.duplicate_batches == 2 and c.positions == {"0": 4}


def test_opaque_offsets_are_resumed_but_not_deduplicated():
    b = _batch([(1, "0", 0, 1.0, "x")])

    class Opaque:
        def read(self, uri, start=None, **options):
            yield StreamOffset({"cursor": "abc"}), b
            yield StreamOffset({"cursor": "abc"}), b

    c, out = _consume(Opaque(), GlobalProfiler(SCHEMA), offset_column=None, partition_column=None)
    assert out[0]["rows"] == 2 and c.source_offset == {"cursor": "abc"} and c.positions == {}


def test_row_level_dedupe_handles_partial_overlaps_and_partitions():
    class Replays:
        def read(self, uri, start=None, **options):
            yield StreamOffset({"a": 3}), _batch([(1, "a", i, 1.0, "x") for i in range(3)])
            # overlaps the first batch by two rows and adds a second partition
            yield (
                StreamOffset({"a": 5, "b": 2}),
                _batch(
                    [(2, "a", 1, 9.0, "x"), (2, "a", 2, 9.0, "x"), (2, "a", 3, 2.0, "y")]
                    + [(2, "a", 4, 2.0, "y"), (3, "b", 0, 3.0, "z"), (3, "b", 1, 3.0, "z")]
                ),
            )
            yield StreamOffset({"a": 5, "b": 2}), _batch([(2, "a", 4, 2.0, "y")])  # whole replay

    c, out = _consume(Replays(), GlobalProfiler(SCHEMA))
    assert out[0]["rows"] == 3 + 2 + 2 and c.duplicate_rows == 2 + 1 and c.duplicate_batches == 1
    assert c.positions == {"a": 5, "b": 2}
    amount = {x["name"]: x for x in out[0]["profile"]["columns"]}["amount"]
    assert amount["max"] == 3.0  # the 9.0 replays never got in


def test_offset_columns_are_validated():
    class One:
        def read(self, uri, start=None, **options):
            yield StreamOffset({"0": 1}), _batch([(1, "0", 0, 1.0, "x")])

    with pytest.raises(TypeError, match="integer column"):
        _consume(One(), GlobalProfiler(SCHEMA), offset_column="cat", partition_column=None)
    with pytest.raises(ValueError, match="needs offset_column"):
        StreamConsumer(One(), "u", GlobalProfiler(SCHEMA), partition_column="partition")
    with pytest.raises(ValueError, match="must be positive"):
        StreamConsumer(One(), "u", GlobalProfiler(SCHEMA), checkpoint_every=0)


def test_a_source_that_only_ever_replays_is_given_up_on():
    """Reconnecting is bounded by consecutive connections that bring nothing new, not by the
    batches they replay."""

    class Stuck:
        def read(self, uri, start=None, **options):  # ignores the offset: always the same batch
            yield StreamOffset({"0": 1}), _batch([(1, "0", 0, 1.0, "x")])
            raise ConnectionError("down")

    c = StreamConsumer(
        Stuck(), "u", GlobalProfiler(SCHEMA), None, offset_column="offset", max_attempts=4
    )
    with pytest.raises(ConnectionError):
        list(c.run())
    assert c.reconnects == 4 and c.batches_processed == 1 and c.duplicate_batches == 3


# ------------------------------------------------- checkpoints and crashes


class Crash(Exception):
    """Not a transport error: the process dies."""


def _crashing(source, after_batches):
    class Wrapped:
        def read(self, uri, start=None, **options):
            for n, item in enumerate(source.read(uri, start, **options)):
                if n == after_batches:
                    raise Crash("killed")
                yield item

    return Wrapped()


@pytest.mark.parametrize(
    "make",
    [_tumbling, lambda **kw: SlidingProfiler(SCHEMA, timedelta(seconds=60), timedelta(seconds=20))],
)
@pytest.mark.parametrize("checkpoint_every", [1, 4])
def test_a_killed_consumer_resumes_from_its_checkpoint(tmp_path, make, checkpoint_every):
    log = make_log(partitions=2, per_partition=500, seed=6)
    want = _consume(LogSource(log, batch_rows=30), make())[1]
    for crash_at in (0, 1, 5, 12, 20, 33):
        path = tmp_path / f"cp-{checkpoint_every}-{crash_at}.json"
        first = []
        c1 = StreamConsumer(
            _crashing(LogSource(log, batch_rows=30), crash_at),
            "log://t",
            make(),
            FileCheckpointStore(path),
            checkpoint_every=checkpoint_every,
            offset_column="offset",
            partition_column="partition",
        )
        with pytest.raises(Crash):
            for w in c1.run():
                first.append(w.to_dict())
        del c1
        c2 = StreamConsumer(
            LogSource(log, batch_rows=30),
            "log://t",
            make(),
            FileCheckpointStore(path),
            checkpoint_every=checkpoint_every,
            offset_column="offset",
            partition_column="partition",
        )
        rest = [w.to_dict() for w in c2.run()]
        # a window handed out before the crash but after the last checkpoint is handed out again;
        # keyed by (kind, start, end) the union is the uninterrupted output, window for window
        merged = {}
        for w in first + rest:
            merged[(w["kind"], w["start_us"], w["end_us"])] = w
        assert sorted(merged) == [(w["kind"], w["start_us"], w["end_us"]) for w in want]
        for w in want:
            _approx(merged[(w["kind"], w["start_us"], w["end_us"])]["profile"], w["profile"])
        assert c2.checkpoints >= 1


def test_a_finished_checkpoint_resumes_to_nothing(tmp_path):
    log = make_log(partitions=1, per_partition=100)
    store = FileCheckpointStore(tmp_path / "cp.json")
    _consume(LogSource(log), _tumbling(), store)
    again = StreamConsumer(LogSource(log), "log://t", _tumbling(), store, offset_column="offset")
    assert list(again.run()) == []
    doc = store.load_document()
    assert doc["format"] == "shape-stream-checkpoint-v1" and doc["offset"] == {"0": 100}


def test_checkpoints_only_resume_the_stream_and_profiler_they_belong_to(tmp_path):
    log = make_log(partitions=1, per_partition=60)
    store = FileCheckpointStore(tmp_path / "cp.json")
    _consume(LogSource(log), _tumbling(), store, checkpoint_every=1)
    with pytest.raises(CheckpointError, match="different profiler"):
        StreamConsumer(
            LogSource(log), "log://t", TumblingProfiler(SCHEMA, timedelta(seconds=7)), store
        )
    with pytest.raises(CheckpointError, match="not 'log://other'"):
        StreamConsumer(LogSource(log), "log://other", _tumbling(), store)
    store.save_document({"format": "something else"})
    with pytest.raises(CheckpointError, match="not a stream consumer checkpoint"):
        StreamConsumer(LogSource(log), "log://t", _tumbling(), store)


def test_the_checkpoint_file_is_replaced_atomically(tmp_path, monkeypatch):
    store = FileCheckpointStore(tmp_path / "cp.json")
    assert store.load_document() is None
    store.save_document({"n": 1})
    real_replace = os.replace

    def fail(src, dst):
        raise OSError("disk went away")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="disk went away"):
        store.save_document({"n": 2})
    monkeypatch.setattr(os, "replace", real_replace)
    assert store.load_document() == {"n": 1}  # the old checkpoint is intact
    assert [p.name for p in tmp_path.iterdir()] == ["cp.json"]  # and no temporary file is left
    (tmp_path / "cp.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(CheckpointError, match="cannot read the checkpoint"):
        store.load_document()
    (tmp_path / "cp.json").write_text("[1]", encoding="utf-8")
    with pytest.raises(CheckpointError, match="not a JSON object"):
        store.load_document()
    with pytest.raises(CheckpointError, match="not a stream checkpoint"):
        store.load()


def test_the_checkpoint_is_json_and_records_the_offset_and_window_state(tmp_path):
    log = make_log(partitions=2, per_partition=100)
    store = FileCheckpointStore(tmp_path / "cp.json")
    c = StreamConsumer(
        LogSource(log, batch_rows=50),
        "log://t",
        _tumbling(),
        store,
        checkpoint_every=2,
        offset_column="offset",
        partition_column="partition",
    )
    it = c.run()
    for _ in range(3):  # a few windows' worth of batches
        next(it, None)
    doc = json.loads((tmp_path / "cp.json").read_text(encoding="utf-8"))
    assert doc["uri"] == "log://t" and set(doc["positions"]) <= {"0", "1"}
    assert doc["offset"] and doc["profiler"]["kind"] == "tumbling"
    assert doc["counters"]["batches_processed"] >= 2
    it.close()
