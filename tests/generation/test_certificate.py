from shape.capture import capture_rows
from shape.generation import certify, geographic_fidelity, relational_fidelity, release_decision
from shape.privacy import k_anonymity


def test_fidelity_certificate_and_release():
    rows = [{"x": i, "state": "OH"} for i in range(100)]
    ref = capture_rows(rows).to_dict()
    c = certify(ref, rows, tolerance=0.01)
    assert c.passed and c.score > 0.99
    k = k_anonymity([{"zip": "1"} for _ in range(10)], ("zip",))
    assert release_decision(c, k, 5).allowed


def test_relational_and_geo():
    p = [{"id": 1}, {"id": 2}]
    c = [{"pid": 1}, {"pid": 1}, {"pid": 2}]
    assert relational_fidelity(p, c, "id", "pid").passed
    g = geographic_fidelity(
        {"OH": 0.5, "PA": 0.5}, [{"state": "OH"}, {"state": "PA"}], tolerance=0.01
    )
    assert g.passed
