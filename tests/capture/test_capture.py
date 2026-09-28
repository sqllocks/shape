from shape.capture import capture_rows


def test_capture():
    s = capture_rows([{"x": 1, "s": "a"}, {"x": 2, "s": "b"}, {"x": None, "s": None}], 2)
    assert s.rows == 3 and s.columns["x"]["null_count"] == 1 and s.columns["s"]["kind"] == "text"
