from shape.capture import capture_rows


def test_late():
    s = capture_rows([{"a": 1}, {"a": 2, "b": "x"}])
    assert s.columns["b"]["count"] == 2 and s.columns["b"]["null_count"] == 1
