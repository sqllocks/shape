from shape.drift import gate


def test_drift_gate():
    a = {"columns": {"x": {"kind": "numeric", "mean": 10}}}
    b = {"columns": {"x": {"kind": "numeric", "mean": 10.1}}}
    assert gate(a, b, 0.1).passed
    assert not gate(a, {"columns": {"x": {"kind": "numeric", "mean": 100}}}, 0.1).passed
