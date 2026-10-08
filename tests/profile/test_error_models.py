from shape.profile import hll_error, kll_error


def test_errors():
    assert hll_error(14).relative_error < 0.02 and kll_error(200).confidence == 0.99
