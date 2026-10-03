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
