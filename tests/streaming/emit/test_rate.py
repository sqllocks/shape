"""Rate schedule and burst parsing (P5-01)."""

from __future__ import annotations

import pytest

from shape.streaming.emit import Burst, RateSchedule, parse_burst


def test_parse_burst() -> None:
    assert parse_burst("10:5:3") == Burst(10.0, 5.0, 3.0)
    assert parse_burst("0:1.5:0.5") == Burst(0.0, 1.5, 0.5)


@pytest.mark.parametrize(
    "spec",
    ["", "1:2", "1:2:3:4", "a:1:2", "-1:1:2", "1:0:2", "1:1:0", "1:1:-2", "1:1:nan", "1:inf:2"],
)
def test_parse_burst_rejects(spec: str) -> None:
    with pytest.raises(ValueError):
        parse_burst(spec)


def test_constant_rate() -> None:
    s = RateSchedule(100)
    assert s.due_time(0) == 0
    assert s.due_time(250) == pytest.approx(2.5)
    assert s.events_by(2.5) == pytest.approx(250)


def test_burst_changes_the_rate_only_inside_it() -> None:
    s = RateSchedule(100, [Burst(2, 3, 4)])  # 100/s, then 400/s from t=2 to t=5, then 100/s
    assert s.events_by(2) == pytest.approx(200)
    assert s.events_by(5) == pytest.approx(200 + 3 * 400)
    assert s.events_by(7) == pytest.approx(1400 + 200)
    for n in (0, 50, 200, 500, 1400, 1401, 5000):
        assert s.events_by(s.due_time(n)) == pytest.approx(n)
    assert s.due_time(200) == pytest.approx(2.0)
    assert s.due_time(600) == pytest.approx(3.0)


def test_burst_at_start_and_slowdown_and_two_bursts() -> None:
    s = RateSchedule(10, [Burst(10, 10, 0.5), Burst(0, 5, 2)])
    assert s.events_by(5) == pytest.approx(100)
    assert s.events_by(10) == pytest.approx(150)
    assert s.events_by(20) == pytest.approx(200)
    assert s.events_by(21) == pytest.approx(210)


def test_overlapping_bursts_and_bad_rate() -> None:
    with pytest.raises(ValueError, match="overlap"):
        RateSchedule(10, [Burst(0, 5, 2), Burst(4, 5, 2)])
    for bad in (0, -1, float("inf"), float("nan")):
        with pytest.raises(ValueError):
            RateSchedule(bad)
