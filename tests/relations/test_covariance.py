from shape.relations import CovarianceProfile


def test_correlation_and_merge():
    a = CovarianceProfile().update([(1, 2), (2, 4)])
    b = CovarianceProfile().update([(3, 6), (4, 8)])
    a.merge(b)
    assert abs(a.correlation - 1) < 1e-12


def test_a_covariance_profile_becomes_a_v2_relationship():
    from shape.spec.model import validate_model

    rel = CovarianceProfile().update([(1, 2), (2, 4), (3, 7)]).to_relationship("x", "y")
    assert rel["kind"] == "correlation" and rel["source"] == "x" and rel["n"] == 3
    doc = {
        "schema_version": 2,
        "engine": "t",
        "mode": "exact",
        "tables": {},
        "relationships": [rel],
    }
    assert validate_model(doc)["relationships"][0]["rho"] > 0.9
