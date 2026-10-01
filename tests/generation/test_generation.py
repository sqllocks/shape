import numpy as np

from shape.capture import capture_columns
from shape.generation import compare_numeric, generate_numeric


def _summary(values):
    return capture_columns({"x": np.asarray(values)})["columns"]["x"]


def test_deterministic_generation():
    s = _summary(range(100))
    assert generate_numeric(s, 10, 7) == generate_numeric(s, 10, 7)


def test_fidelity_report_multidimensional_shape():
    s = _summary(range(100))
    g = generate_numeric(s, 5000, 7)
    o = _summary(g)
    r = compare_numeric(s, o)
    assert "marginal" in r.dimensions
