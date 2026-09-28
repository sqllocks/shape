from shape.generation import compare_numeric, generate_numeric
from shape.profile import NumericProfile


def test_deterministic_generation():
    s = NumericProfile().update(range(100)).summary()
    assert generate_numeric(s, 10, 7) == generate_numeric(s, 10, 7)


def test_fidelity_report_multidimensional_shape():
    s = NumericProfile().update(range(100)).summary()
    g = generate_numeric(s, 5000, 7)
    o = NumericProfile().update(g).summary()
    r = compare_numeric(s, o)
    assert "marginal" in r.dimensions
