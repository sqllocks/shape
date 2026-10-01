import json
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


def test_s3_snapshot_restores_to_an_identical_window():
    """S3: ``TumblingWindow`` had ``snapshot()`` but no way back."""
    t = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    events = [(t, "a"), (t + timedelta(seconds=30), "b"), (t + timedelta(minutes=3), "c")]
    tail = [(t + timedelta(seconds=10), "late"), (t + timedelta(minutes=5), "d")]
    live = TumblingWindow(timedelta(minutes=1), timedelta(seconds=10))
    for ts, v in events:
        live.add(ts, v)
    revived = TumblingWindow.restore(json.loads(json.dumps(live.snapshot())))  # killed here
    assert revived.snapshot() == live.snapshot()
    for w in (live, revived):
        for ts, v in tail:
            w.add(ts, v)
    assert revived.close_ready() == live.close_ready()
    assert revived.snapshot() == live.snapshot() and revived.late_dropped == live.late_dropped == 1


def test_restoring_an_empty_window():
    w = TumblingWindow(timedelta(seconds=5))
    back = TumblingWindow.restore(w.snapshot())
    assert back.watermark is None and back.snapshot() == w.snapshot()
