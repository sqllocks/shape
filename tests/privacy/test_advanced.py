from shape.privacy.advanced import k_anonymous, reidentification_risk


def test_privacy():
    assert (
        len(k_anonymous({"a": 1, "b": 5}, 2)) == 1
        and 0 <= reidentification_risk({"a": 1, "b": 2}) <= 1
    )


def test_no_laplace_mechanism():
    """SEC1 / D-07: the fixed-seed Laplace 'DP' was removed."""
    import shape.privacy
    import shape.privacy.advanced as adv

    assert not hasattr(adv, "laplace")
    assert not hasattr(shape.privacy, "laplace")
