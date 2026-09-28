from datetime import UTC, datetime, timedelta

from shape.streaming import AggregateTumblingWindow


def test_aggregate_window_bounded_state():
    w = AggregateTumblingWindow(timedelta(seconds=10), timedelta(seconds=2))
    b = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(10000):
        w.add(b + timedelta(milliseconds=i), i)
    assert len(w._windows) <= 2
    s = next(iter(w._windows.values()))
    assert s.count > 0 and not hasattr(s, "values")


def test_aggregate_window_late_drop():
    w = AggregateTumblingWindow(timedelta(seconds=10), timedelta(seconds=1))
    b = datetime(2026, 1, 1, tzinfo=UTC)
    assert w.add(b + timedelta(seconds=20), 1)
    assert not w.add(b, 2)
    assert w.late_dropped == 1
