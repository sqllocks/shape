from shape.validation.suite import conformance


def test_conformance():
    r = conformance()
    assert r and all(x.passed for x in r)
