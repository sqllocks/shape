from shape.validation.adversarial import hostile_strings, numeric_edges


def test_corpus():
    assert len(hostile_strings()) >= 10 and any(str(x) == "nan" for x in numeric_edges())
