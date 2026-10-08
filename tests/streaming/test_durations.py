"""ISS-stream (#34): durations of the stream API are unambiguous.

A bare integer used to be read as microseconds, so ``TumblingProfiler(schema, 60_000)`` made
60 ms windows without a word. A duration is now a ``timedelta`` or a string with a unit
(``"60s"``, ``"5m"``, as the CLI takes it); a bare number other than zero is refused, so no
existing call can change its results silently.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pyarrow as pa
import pytest

from shape.streaming.runtime import (
    SessionProfiler,
    SlidingProfiler,
    TumblingProfiler,
    parse_duration,
    restore_profiler,
)

SCHEMA = pa.schema([("x", pa.int64()), ("_shape_event_time", pa.timestamp("us", tz="UTC"))])
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _six_minutes() -> pa.RecordBatch:
    times = [T0 + timedelta(seconds=s) for s in range(360)]
    return pa.RecordBatch.from_pydict(
        {"x": list(range(360)), "_shape_event_time": pa.array(times, pa.timestamp("us", tz="UTC"))},
        schema=SCHEMA,
    )


def _windows(profiler) -> int:
    return len(profiler.process(_six_minutes())) + len(profiler.finish())


def test_the_issue_example_gives_six_windows_in_every_spelling():
    for size in (timedelta(seconds=60), "60s", "1m", " 1 m "):
        assert _windows(TumblingProfiler(SCHEMA, size)) == 6, size


def test_a_bare_integer_is_refused_not_read_as_microseconds():
    with pytest.raises(ValueError, match=r"timedelta\(seconds=60\).*'60s'") as err:
        TumblingProfiler(SCHEMA, 60_000)
    assert "size" in str(err.value)
    with pytest.raises(ValueError, match="allowed_lateness"):
        TumblingProfiler(SCHEMA, "1m", allowed_lateness=5)
    with pytest.raises(ValueError, match="slide"):
        SlidingProfiler(SCHEMA, "1m", 30)
    with pytest.raises(ValueError, match="gap"):
        SessionProfiler(SCHEMA, 30)
    with pytest.raises(ValueError, match="offset"):
        TumblingProfiler(SCHEMA, "1m", offset=1_000)
    with pytest.raises(ValueError, match="bare number"):
        TumblingProfiler(SCHEMA, 1.5)


@pytest.mark.parametrize("value", [True, None, b"60s", [60]])
def test_other_types_are_refused(value):
    with pytest.raises(ValueError, match="duration"):
        TumblingProfiler(SCHEMA, value)


def test_zero_is_the_same_in_every_unit_so_it_stays_allowed():
    p = TumblingProfiler(SCHEMA, "1m", allowed_lateness=0, offset=0)
    assert p.allowed_lateness == 0 and p.offset == 0


def test_strings_take_the_cli_units():
    for text, us in [("500ms", 500_000), ("2h", 7_200_000_000), ("1d", 86_400_000_000)]:
        assert parse_duration(text, "x") == us
    assert parse_duration("250us", "x") == 250
    assert parse_duration("5", "x") == 5_000_000  # a bare numeric string is seconds, as in the CLI
    with pytest.raises(ValueError, match="not a duration"):
        parse_duration("soon", "x")


def test_negative_and_zero_sizes_still_fail_in_every_spelling():
    with pytest.raises(ValueError, match="positive"):
        TumblingProfiler(SCHEMA, "0s")
    with pytest.raises(ValueError, match="zero or more"):
        TumblingProfiler(SCHEMA, "1m", allowed_lateness=timedelta(seconds=-1))


def test_snapshots_written_before_this_change_still_restore():
    p = TumblingProfiler(SCHEMA, "1m", allowed_lateness="5s")
    p.process(_six_minutes())
    snap = p.snapshot()
    assert snap["config"]["size_us"] == 60_000_000 and snap["allowed_lateness_us"] == 5_000_000
    q = restore_profiler(snap)
    assert (q.size, q.allowed_lateness) == (60_000_000, 5_000_000)
    s = restore_profiler(SessionProfiler(SCHEMA, "30s", allowed_lateness="2s").snapshot())
    assert (s.gap, s.allowed_lateness) == (30_000_000, 2_000_000)
    w = restore_profiler(SlidingProfiler(SCHEMA, "1m", "20s", offset="5s").snapshot())
    assert (w.size, w.slide, w.offset) == (60_000_000, 20_000_000, 5_000_000)
