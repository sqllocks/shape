from shape.privacy.advanced import k_anonymous, reidentification_risk


def test_privacy():
    assert (
        len(k_anonymous({"a": 1, "b": 5}, 2)) == 1
        and 0 <= reidentification_risk({"a": 1, "b": 2}) <= 1
    )
