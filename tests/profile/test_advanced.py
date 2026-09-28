from shape.profile.advanced import entropy, infer_pattern, missingness_dependency, pearson


def test_advanced():
    rows = [
        {"a": 1, "b": 2, "x": None, "y": None},
        {"a": 2, "b": 4, "x": "v", "y": "v"},
        {"a": 3, "b": 6, "x": None, "y": None},
    ]
    assert pearson(rows, "a", "b") > 0.99
    assert missingness_dependency(rows, "x", "y").phi == 1
    assert entropy(["a", "a", "b"]) > 0
    assert infer_pattern(["AB-123", "CD-456"])[0][0] == "A-D"
