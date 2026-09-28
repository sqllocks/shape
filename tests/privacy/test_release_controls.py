from shape.privacy import differencing_risk, suppress_shape


def test_release():
    assert suppress_shape({"rows": 3, "columns": {"x": {"kind": "text", "count": 3}}})["columns"][
        "x"
    ]["suppressed"]
    assert differencing_risk({"rows": 10}, {"rows": 12})["risky"]
