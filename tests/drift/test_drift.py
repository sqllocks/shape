from shape.drift import compare


def test_drift():
    a = {"columns": {"x": {"kind": "numeric", "mean": 10, "null_count": 0}}}
    b = {"columns": {"x": {"kind": "numeric", "mean": 20, "null_count": 0}, "y": {"kind": "text"}}}
    d = compare(a, b)
    assert d[0].score == 1 and any(x.path == "columns.x.mean" for x in d)
