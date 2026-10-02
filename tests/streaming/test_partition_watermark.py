"""Issue #41: the watermark is kept per partition, so a normal multi-partition stream loses
nothing with the default allowed lateness, and the events that are still late are reported."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

from shape.cli.main import main
from shape.plugins.api.v1 import StreamOffset
from shape.streaming import cli as stream_cli
from shape.streaming.consumer import StreamConsumer
from shape.streaming.messages import (
    StreamMessage,
    conform,
    decode_messages,
    freeze,
    partition_of,
)
from shape.streaming.runtime import (
    DEFAULT_MAX_PARTITION_SKEW,
    EVENT_TIME,
    TumblingProfiler,
    WindowedProfiler,
    restore_profiler,
)

SEC = 1_000_000
T0 = 1_700_000_000_000  # an event time in milliseconds
SCHEMA = pa.schema([("id", pa.int64()), (EVENT_TIME, pa.timestamp("us", tz="UTC"))])


def _batch(partition: str | None, seconds: range, per_second: int = 10, start_id: int = 0):
    """Events of one partition, ``per_second`` inside every second of ``seconds``."""
    messages = []
    n = start_id
    for sec in seconds:
        for i in range(per_second):
            ms = T0 + sec * 1000 + i * (1000 // per_second)
            body = json.dumps({"id": n, "event_time": ms})
            messages.append(StreamMessage(partition or "0", n, body, None))
            n += 1
    batch = decode_messages(messages, event_time_field="event_time", event_time_unit="ms")
    assert batch is not None
    return batch


def _delivery(parts: int, seconds: int, chunk: int, per_second: int = 10):
    """A hub of ``parts`` partitions read ``chunk`` seconds at a time, round robin: partition 0
    is ``chunk`` seconds of event time ahead of partition 1 while they are read."""
    batches = []
    for lo in range(0, seconds, chunk):
        for p in range(parts):
            batches.append(
                (
                    str(p),
                    _batch(
                        str(p),
                        range(lo, min(lo + chunk, seconds)),
                        per_second,
                        start_id=p * 1_000_000 + lo * per_second,
                    ),
                )
            )
    return batches


class _Source:
    """A stream source over fixed batches, each with the ``{partition: next offset}`` position."""

    name = "fake"
    schemes = ("fake",)

    def __init__(self, parts: int, batches: list[tuple[str, pa.RecordBatch]]) -> None:
        self.parts = parts
        self.batches = batches
        self.stats = None

    def read(self, uri: str, start: Any = None, **options: Any):
        positions = {str(p): 0 for p in range(self.parts)}
        for pid, batch in self.batches:
            positions[pid] += batch.num_rows
            yield StreamOffset(dict(positions)), batch


def _rows(path: Path) -> int:
    return sum(json.loads(line)["rows"] for line in path.read_text().splitlines())


def _run_cli(
    source: _Source,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *extra: str,
    follow: bool = True,
):
    monkeypatch.setattr(stream_cli, "find_source", lambda uri: source)
    out, err = io.StringIO(), io.StringIO()
    argv = [
        "stream-profile",
        "fake://x/t",
        "--window",
        "tumbling",
        "--size",
        "2s",
        "--windows",
        str(tmp_path / "w.jsonl"),
        "--event-time",
        "event_time",
        *extra,
    ]
    if follow:
        argv += ["--follow", "--idle-timeout", "1"]
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    assert code == 0, err.getvalue()
    return json.loads(out.getvalue().splitlines()[-1]), err.getvalue()


# ---- the metadata the sources already carry --------------------------------------------------


def test_a_decoded_batch_knows_its_partition_and_conform_keeps_it():
    batch = _batch("3", range(2))
    assert partition_of(batch) == "3"
    assert partition_of(conform(batch, freeze(batch))) == "3"
    mixed = decode_messages(
        [StreamMessage("0", 0, '{"a": 1}', 1), StreamMessage("1", 0, '{"a": 2}', 2)]
    )
    assert mixed is not None and partition_of(mixed) is None
    assert partition_of(pa.record_batch({"a": [1]})) is None


# ---- the profiler ----------------------------------------------------------------------------


def _profile(batches, *, per_partition: bool, skew: int | None = None, lateness: int = 0):
    prof = TumblingProfiler(SCHEMA, 2 * SEC, allowed_lateness=lateness, name="t")
    if skew is not None:
        prof.max_partition_skew = skew
    if per_partition:
        prof.register_partitions(sorted({p for p, _ in batches}))
    windows = []
    for pid, batch in batches:
        windows += prof.process(batch.replace_schema_metadata(None), pid if per_partition else None)
    windows += prof.finish()
    return prof, windows


def test_interleaving_alone_makes_events_late_without_partitions():
    batches = _delivery(4, 20, chunk=5)
    prof, windows = _profile(batches, per_partition=False)
    assert prof.late_events > 0  # the filed behaviour: the newest partition outruns the others
    assert sum(w.rows for w in windows) == 800 - prof.late_events


@pytest.mark.parametrize("chunk", [1, 2, 5, 10, 20])
def test_per_partition_watermarks_lose_nothing_by_default(chunk: int):
    batches = _delivery(4, 20, chunk=chunk)
    prof, windows = _profile(batches, per_partition=True)
    assert prof.late_events == 0
    assert sum(w.rows for w in windows) == 4 * 20 * 10
    assert prof.windows_emitted == len(windows)


def test_the_profile_is_the_same_whatever_the_interleaving():
    def totals(chunk: int):
        _, windows = _profile(_delivery(3, 12, chunk=chunk), per_partition=True)
        return [(w.start, w.end, w.rows) for w in windows]

    assert totals(1) == totals(3) == totals(12)


def test_one_partition_behaves_as_before():
    batches = [("0", _batch("0", range(s, s + 2))) for s in range(0, 20, 2)]
    plain, w_plain = _profile(batches, per_partition=False)
    one, w_one = _profile(batches, per_partition=True)
    assert [(w.start, w.end, w.rows) for w in w_plain] == [(w.start, w.end, w.rows) for w in w_one]
    assert plain.late_events == one.late_events == 0
    assert plain.watermark == one.watermark


def test_windows_close_as_the_slowest_partition_advances():
    prof = TumblingProfiler(SCHEMA, 2 * SEC, name="t")
    prof.register_partitions(["0", "1"])
    assert prof.process(_batch("0", range(10)), "0") == []
    assert prof.process(_batch("0", range(10, 20)), "0") == []  # partition 1 has said nothing
    closed = prof.process(_batch("1", range(6), start_id=500), "1")  # it is at 5.9 s
    assert [w.end for w in closed] == [(T0 // 1000 + s) * SEC for s in (2, 4)]


def test_a_silent_partition_holds_windows_for_at_most_the_skew_cap():
    prof = TumblingProfiler(SCHEMA, 2 * SEC, name="t")
    prof.max_partition_skew = 30 * SEC
    prof.register_partitions(["0", "1"])
    closed = prof.process(_batch("0", range(20)), "0")
    assert closed == []  # 20 s of event time: still within the cap, partition 1 may still come
    closed = prof.process(_batch("0", range(20, 60)), "0")
    assert closed and closed[-1].end <= (T0 // 1000 + 30) * SEC
    # the silent partition delivers events from before the cap: late, counted, never silent
    before = prof.late_events
    prof.process(_batch("1", range(2), start_id=900), "1")
    assert prof.late_events == before + 20
    assert prof.max_late_lag > 0


def test_the_default_skew_cap_is_ten_minutes():
    assert DEFAULT_MAX_PARTITION_SKEW == 600 * SEC
    assert TumblingProfiler(SCHEMA, SEC).max_partition_skew == 600 * SEC


def test_an_idle_partition_stops_holding_the_watermark_and_comes_back():
    prof = TumblingProfiler(SCHEMA, 2 * SEC, name="t")
    prof.register_partitions(["0", "1"])
    assert prof.process(_batch("0", range(10)), "0") == []
    prof.set_idle("1")
    assert prof.process(_batch("0", range(10, 12)), "0")  # windows close without partition 1
    prof.process(_batch("1", range(11, 12), start_id=700), "1")  # it delivers: active again
    assert "1" not in prof._idle


def test_late_rows_record_how_far_behind_they_were():
    prof = TumblingProfiler(SCHEMA, 2 * SEC, name="t")
    prof.process(_batch(None, range(30)))
    assert prof.late_events == 0
    prof.process(_batch(None, range(0, 4), start_id=900))
    assert prof.late_events == 40
    assert prof.max_late_lag >= 25 * SEC


def test_a_snapshot_restores_the_partitions_and_the_run_is_exact():
    batches = _delivery(3, 20, chunk=4)
    whole, w_whole = _profile(batches, per_partition=True)
    prof = TumblingProfiler(SCHEMA, 2 * SEC, name="t")
    prof.register_partitions(["0", "1", "2"])
    seen = []
    for pid, batch in batches[:5]:
        seen += prof.process(batch, pid)
    prof = restore_profiler(json.loads(json.dumps(prof.snapshot())))
    assert sorted(prof.partitions) == ["0", "1", "2"]
    for pid, batch in batches[5:]:
        seen += prof.process(batch, pid)
    seen += prof.finish()
    assert [(w.start, w.end, w.rows) for w in seen] == [(w.start, w.end, w.rows) for w in w_whole]
    assert prof.late_events == 0


def test_an_old_snapshot_without_partitions_still_restores():
    prof = TumblingProfiler(SCHEMA, 2 * SEC, name="t")
    prof.process(_batch(None, range(4)))
    snap = prof.snapshot()
    del snap["partitions"]
    del snap["counters"]["max_late_lag_us"]
    back = restore_profiler(snap)
    assert isinstance(back, WindowedProfiler) and back.partitions == {}
    assert back.watermark == prof.watermark


# ---- the consumer and the command ------------------------------------------------------------


def test_the_consumer_learns_the_partitions_from_the_offsets():
    batches = _delivery(4, 20, chunk=5)
    prof = TumblingProfiler(SCHEMA, 2 * SEC, name="t")
    consumer = StreamConsumer(_Source(4, batches), "fake://x/t", prof)
    windows = list(consumer.run())
    assert sorted(prof.partitions) == ["0", "1", "2", "3"]
    assert prof.late_events == 0
    assert sum(w.rows for w in windows) == 800


def _first_window_after(consumer: StreamConsumer, prof: WindowedProfiler) -> int:
    """How many batches the profiler had taken when the first window came out."""
    for _window in consumer.run():
        return prof.batches
    raise AssertionError("no window was closed")


def test_the_consumer_marks_a_quiet_partition_idle_after_the_timeout():
    # partition 1 is in every offset but never delivers; each batch comes ten seconds after the
    # last, so a 25 s timeout takes partition 1 out of the watermark after a few batches
    batches = [("0", _batch("0", range(s, s + 2))) for s in range(0, 20, 2)]
    clock = iter(range(0, 10_000, 10))
    prof = TumblingProfiler(SCHEMA, 2 * SEC, name="t")
    consumer = StreamConsumer(
        _Source(2, batches),
        "fake://x/t",
        prof,
        partition_idle_timeout=25,
        clock=lambda: float(next(clock)),
    )
    assert _first_window_after(consumer, prof) < len(batches)
    # without the timeout the windows wait for partition 1 (here until the end of the stream)
    prof2 = TumblingProfiler(SCHEMA, 2 * SEC, name="t")
    consumer2 = StreamConsumer(_Source(2, batches), "fake://x/t", prof2)
    assert _first_window_after(consumer2, prof2) == len(batches)


def test_the_command_loses_nothing_by_default_on_a_skewed_hub(tmp_path, monkeypatch):
    source = _Source(4, _delivery(4, 20, chunk=10))
    summary, err = _run_cli(source, tmp_path, monkeypatch)
    assert summary["events"] == 800 and summary["late_events"] == 0
    assert _rows(tmp_path / "w.jsonl") == 800
    assert "late" not in err and "late_share" not in summary


def test_a_bounded_read_loses_nothing_either(tmp_path, monkeypatch):
    # a bounded read takes the partitions one after the other: event time starts over at each
    batches = [(str(p), _batch(str(p), range(20), start_id=p * 10_000)) for p in range(4)]
    summary, _ = _run_cli(_Source(4, batches), tmp_path, monkeypatch, follow=False)
    assert summary["events"] == 800 and summary["late_events"] == 0
    assert _rows(tmp_path / "w.jsonl") == 800


def test_late_events_are_reported_with_a_suggested_lateness(tmp_path, monkeypatch):
    # partition 1 starts 90 s behind partition 0: beyond a 60 s skew cap, so its rows are late
    batches = [("0", _batch("0", range(90, 150))), ("1", _batch("1", range(0, 30), start_id=5000))]
    summary, err = _run_cli(
        _Source(2, batches), tmp_path, monkeypatch, "--max-partition-skew", "60s"
    )
    assert summary["late_events"] > 0
    assert summary["late_share"] > 0.01
    assert summary["late_lag_seconds"] > 0
    assert err.startswith("shape: warning: ")
    assert f"{summary['late_events']:,} of {summary['events']:,} events" in err
    assert "--allowed-lateness " in err
    assert _rows(tmp_path / "w.jsonl") == summary["events"] - summary["late_events"]
    # the suggestion keeps them
    suggested = err.split("--allowed-lateness ")[1].split("s ")[0]
    (tmp_path / "w.jsonl").unlink()
    again, err2 = _run_cli(
        _Source(2, batches),
        tmp_path,
        monkeypatch,
        "--max-partition-skew",
        "60s",
        "--allowed-lateness",
        f"{suggested}s",
    )
    assert again["late_events"] == 0 and "late" not in err2
    assert _rows(tmp_path / "w.jsonl") == again["events"]


def test_a_handful_of_late_events_is_a_note_not_a_warning(tmp_path, monkeypatch):
    batches = [
        ("0", _batch("0", range(0, 400), per_second=20)),
        ("0", _batch("0", range(0, 1), per_second=2, start_id=9000)),
    ]
    summary, err = _run_cli(_Source(1, batches), tmp_path, monkeypatch)
    assert 0 < summary["late_events"] and summary["late_share"] < 0.01
    assert err.startswith("shape: note: ")


def test_the_options_are_validated(tmp_path, monkeypatch):
    source = _Source(1, [("0", _batch("0", range(4)))])
    monkeypatch.setattr(stream_cli, "find_source", lambda uri: source)
    err = io.StringIO()
    with redirect_stderr(err), redirect_stdout(io.StringIO()):
        code = main(
            [
                "stream-profile",
                "fake://x/t",
                "--window",
                "tumbling",
                "--size",
                "2s",
                "--windows",
                str(tmp_path / "w.jsonl"),
                "--event-time",
                "event_time",
                "--follow",
                "--idle-timeout",
                "1",
                "--partition-idle-timeout",
                "-1",
            ]
        )
    assert code == 2 and "--partition-idle-timeout cannot be negative" in err.getvalue()
