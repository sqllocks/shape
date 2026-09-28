from datetime import UTC, datetime, timedelta

from shape.streaming.windows import TumblingWindow


def test_window_and_lateness():
    w = TumblingWindow(timedelta(minutes=1), timedelta(seconds=10))
    t = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert w.add(t, "a")
    assert w.add(t + timedelta(minutes=2), "b")
    ready = w.close_ready()
    assert len(ready) == 1 and ready[0].values == ("a",)
    assert not w.add(t, "late") and w.late_dropped == 1
