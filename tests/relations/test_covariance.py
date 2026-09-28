from shape.relations import CovarianceProfile


def test_correlation_and_merge():
    a = CovarianceProfile().update([(1, 2), (2, 4)])
    b = CovarianceProfile().update([(3, 6), (4, 8)])
    a.merge(b)
    assert abs(a.correlation - 1) < 1e-12
