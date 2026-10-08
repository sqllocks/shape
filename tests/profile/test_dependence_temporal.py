from shape.profile import lag_autocorrelation, normalized_mutual_information


def test_nonlinear_dependence_detected():
    rows = [{"x": i / 100, "y": (i / 100) ** 2} for i in range(-500, 501)]
    assert normalized_mutual_information(rows, "x", "y") > 0.5


def test_temporal_order_evidence():
    assert lag_autocorrelation(list(range(1000))) > 0.99
