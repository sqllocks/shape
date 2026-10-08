from shape.privacy import derived_classification


def test_no_downgrade():
    assert derived_classification(["PUBLIC", "SECRET"]) == "SECRET"
    assert derived_classification(["SECRET"], "PUBLIC") == "SECRET"
    assert derived_classification(["CONFIDENTIAL"], "TOP_SECRET") == "TOP_SECRET"
