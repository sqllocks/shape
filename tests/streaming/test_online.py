from shape.streaming.online import OnlineShape


def test_online():
    s = OnlineShape(2)
    for i in range(5):
        s.add({"x": i})
    x = s.snapshot()
    assert x["stream_rows_seen"] == 5 and x["window_rows"] == 2
