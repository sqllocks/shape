from shape.privacy.measure import k_anonymity, l_diversity


def test_privacy_measurements():
    rows = [
        {"zip": "1", "age": 20, "dx": "a"},
        {"zip": "1", "age": 20, "dx": "b"},
        {"zip": "2", "age": 30, "dx": "c"},
    ]
    k = k_anonymity(rows, ("zip", "age"))
    assert k.k == 1 and k.unique_groups == 1
    level = l_diversity(rows, ("zip", "age"), "dx", 2)
    assert level.minimum_l == 1 and level.violating_groups == 1
