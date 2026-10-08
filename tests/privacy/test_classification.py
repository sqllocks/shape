from shape.privacy import ClassificationTaxonomy


def test_tax():
    t = ClassificationTaxonomy()
    assert t.join("PUBLIC", "SECRET") == "SECRET"
    assert t.permits("SECRET", "TOP_SECRET")
    assert not t.permits("TOP_SECRET", "SECRET")
