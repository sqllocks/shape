"""Regression tests for the defects the streaming audit (AUD-stream) found; each test names its
issue."""

import json

import pytest

from shape.cli.main import main
from shape.streaming import file_source


def _events(path, n=50):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for i in range(n):
            f.write(json.dumps({"v": i, "_shape_event_time": f"2026-01-01T00:{i:02d}:00"}) + "\n")


def _windows(path):
    return [(d["start"], d["rows"]) for d in map(json.loads, path.read_text().splitlines())]


def test_153_interrupt_then_resume_writes_complete_windows(tmp_path, monkeypatch, capsys):
    events = tmp_path / "ev" / "a.jsonl"
    _events(events)
    args = ["stream-profile", str(events), "--window", "tumbling", "--size", "10m"]
    args += ["--batch-size", "15", "--checkpoint-every", "1"]
    whole = tmp_path / "whole.jsonl"
    assert main([*args, "--windows", str(whole)]) == 0

    original = file_source.FileStreamSource.read
    seen = {"n": 0}

    def interrupted(self, uri, start=None, **options):
        for item in original(self, uri, start, **options):
            seen["n"] += 1
            if seen["n"] == 2 and start is None:
                raise KeyboardInterrupt
            yield item

    monkeypatch.setattr(file_source.FileStreamSource, "read", interrupted)
    resumed = tmp_path / "resumed.jsonl"
    checkpoint = tmp_path / "ck.json"
    run = [*args, "--windows", str(resumed), "--checkpoint", str(checkpoint)]
    assert main(run) == 0
    assert main(run) == 0
    capsys.readouterr()
    assert _windows(resumed) == _windows(whole)


@pytest.mark.parametrize("value", ["1e30", "99999999999999999999", "-1e30"])
def test_154_an_out_of_range_event_time_is_not_a_time(value):
    from shape.streaming.messages import DecodeStats, StreamMessage, decode_messages

    stats = DecodeStats()
    batch = decode_messages(
        [
            StreamMessage("0", 0, '{"x": 1}', timestamp_us=5),
            StreamMessage("0", 1, '{"x": 2, "_shape_event_time": ' + value + "}", timestamp_us=7),
        ],
        stats=stats,
    )
    assert batch is not None and batch.num_rows == 2
    times = batch.column("_shape_event_time").cast("int64").to_pylist()
    assert times == [5, 7]  # the broker's time, as for any value that is not a valid time


@pytest.mark.parametrize("cls", ["TumblingWindow", "AggregateTumblingWindow"])
@pytest.mark.parametrize("size_ms", [100, 300, 700, 1100])
def test_155_tumbling_window_start_contains_the_event(cls, size_ms):
    from datetime import UTC, datetime, timedelta

    import shape.streaming as streaming

    w = getattr(streaming, cls)(timedelta(milliseconds=size_ms))
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for ms in range(0, 20_000, 100):
        t = base + timedelta(milliseconds=ms)
        start = w._start(t)
        assert start <= t < start + w.size, (ms, start)


def test_156_the_value_anomaly_changes_one_value_per_row():
    import pyarrow as pa

    from shape.streaming.emit.anomaly import ValueAnomalyMutator

    n = 64
    batch = pa.record_batch(
        {
            "a": pa.array([None, 3] * (n // 2), pa.int64()),
            "b": pa.array([2**60 + 1] * n, pa.int64()),
            "c": pa.array([None, 1.5] * (n // 2), pa.float64()),
            "d": pa.array(["x", None] * (n // 2), pa.string()),
        }
    )
    out, _ = ValueAnomalyMutator().mutate(batch, 7)
    before, after = batch.to_pylist(), out.to_pylist()
    for old, new in zip(before, after, strict=True):
        changed = [k for k in old if old[k] != new[k] and not (old[k] is None and new[k] is None)]
        assert len(changed) <= 1, (old, new)
        assert all(not (isinstance(v, float) and v != v) for v in new.values()), new  # no NaN
        b = new["b"]  # an outlier is -1, 100 or 1000 times the value: never a wrap past int64
        assert b in (None, old["b"], -(2**60)) or b > 2**62, new


def test_157_an_expired_key_that_returns_starts_a_new_sketch():
    from shape.streaming import KeyedSketches

    s = KeyedSketches(100, ttl=16.0)
    s.update(["a"], [1.0], [0.0])
    s.update(["b"], [1.0], [15.5])
    s.update(["c"], [1.0], [16.2])
    assert "a" not in s  # idle for 16.2 >= ttl, although not swept yet
    s.update(["a"], [5.0], [16.3])
    got = s.summary("a")
    assert got is not None
    assert (got["count"], got["first_time"], got["mean"]) == (1, 16.3, 5.0)


@pytest.mark.parametrize("first", [[], [None, None]])
def test_158_a_batch_without_keys_does_not_fix_the_key_kind(first):
    from shape.streaming import Deduplicator

    d = Deduplicator()
    assert d.filter(first).tolist() == [True] * len(first)
    assert d.filter([1, 2, 1]).tolist() == [True, True, False]
    with pytest.raises(TypeError):
        d.filter(["x"])  # the kind is now int


def test_159_a_torn_last_window_line_is_cut_on_restart(tmp_path, capsys):
    events = tmp_path / "ev" / "a.jsonl"
    _events(events)
    args = ["stream-profile", str(events), "--window", "tumbling", "--size", "10m", "--windows"]
    whole = tmp_path / "whole.jsonl"
    assert main([*args, str(whole)]) == 0
    lines = whole.read_text().splitlines(keepends=True)
    torn = tmp_path / "torn.jsonl"
    torn.write_text("".join(lines[:2]) + lines[2][:40])  # killed while writing the third
    assert main([*args, str(torn)]) == 0
    capsys.readouterr()
    assert torn.read_text() == whole.read_text()


def test_160_a_failed_file_write_leaves_the_file_as_it_was(tmp_path):
    import pyarrow as pa

    from shape.streaming.emit.formats import encode_batch, with_event_fields
    from shape.streaming.emit.sinks import FileSink

    batch = with_event_fields(pa.record_batch({"v": list(range(5))}), "t", 0)
    path = tmp_path / "e.jsonl"
    sink = FileSink(path)
    sink.send(batch.slice(0, 2))
    real = sink._f

    class Full:  # the disk fills part way through the write, once
        failed = False

        def write(self, data):
            if not Full.failed:
                Full.failed = True
                real.write(data[: len(data) // 2])
                real.flush()
                raise OSError(28, "No space left on device")
            return real.write(data)

        def __getattr__(self, name):
            return getattr(real, name)

    sink._f = Full()
    with pytest.raises(OSError):
        sink.send(batch.slice(2))
    sink.send(batch.slice(2))  # the runtime's retry
    sink._f = real
    sink.close()
    assert path.read_bytes() == encode_batch(batch)


def test_161_deduplicate_ids_keeps_ids_of_different_types_apart():
    from shape.streaming import deduplicate_ids

    seen: set = set()
    keep = deduplicate_ids([1, "1", 1, 2.5, "2.5"], seen)
    assert keep.tolist() == [True, True, False, True, True]
    assert seen == {1, "1", 2.5, "2.5"}
    assert deduplicate_ids([True, 1, 1.0], set()).tolist() == [True, False, False]  # equal in a set


def test_162_a_retry_after_a_failed_duplicate_resends_only_what_failed():
    from collections import Counter

    import pyarrow as pa

    from shape.streaming.emit.faults import FaultSink
    from shape.streaming.emit.formats import with_event_fields

    class Flaky:
        def __init__(self):
            self.keys = []
            self.failed = False

        def send(self, batch):
            if batch.num_rows == 1 and not self.failed:  # the first duplicate copy fails
                self.failed = True
                raise ConnectionError("dropped")
            self.keys += batch.column("_shape_seq").to_pylist()

        def flush(self):
            pass

        def close(self):
            pass

    inner = Flaky()
    sink = FaultSink(inner, seed=1, duplicate_fraction=1.0, duplicate_window=1)
    events = with_event_fields(pa.record_batch({"v": list(range(6))}), "t", 0)
    first, second = events.slice(0, 3), events.slice(3)
    sink.send(first)
    with pytest.raises(ConnectionError):
        sink.send(second)  # delivered, then the first copy due fails
    sink.send(second)  # the runtime retries the call
    sink.close()
    assert inner.failed
    assert Counter(inner.keys) == {seq: 2 for seq in range(6)}  # each event and one copy
