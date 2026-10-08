from shape.capture import capture_rows
from shape.streaming import ShapeMonitor


def test_monitor():
    r = capture_rows([{"x": 1}, {"x": 2}]).to_dict()
    m = ShapeMonitor(r, every=2)
    assert m.add({"x": 1}) is None and m.add({"x": 2}).rows_seen == 2
