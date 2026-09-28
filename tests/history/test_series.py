from shape.generation import ShapePoint
from shape.history import ShapeSeries


def test_series():
    s = ShapeSeries(
        (
            ShapePoint(0, {"columns": {"x": {"mean": 0}}}),
            ShapePoint(10, {"columns": {"x": {"mean": 10}}}),
        )
    )
    assert s.at(5)["columns"]["x"]["mean"] == 5 and len(list(s.replay(0, 10, 5))) == 3
