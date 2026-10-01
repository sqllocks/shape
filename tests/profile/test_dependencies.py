from shape.profile.dependencies import candidate_key, functional_dependency


def test_fd_and_key():
    rows = [
        {"zip": "1", "state": "OH", "id": 1},
        {"zip": "1", "state": "OH", "id": 2},
        {"zip": "2", "state": "PA", "id": 3},
    ]
    fd = functional_dependency(rows, ("zip",), "state")
    assert fd.confidence == 1 and fd.violating_groups == 0
    k = candidate_key(rows, ("id",))
    assert k.unique


def test_p16_fd_confidence_is_not_inflated_when_the_group_table_is_bounded():
    """P16: the bounded version sampled per row, not per group, so a dependency that fails in
    every group looked strong. Every group here has two dependent values (confidence exactly
    one half); bounding the table to 10 groups must say so and must not raise the figure."""
    from shape.profile.dependencies import functional_dependency

    rows = [{"k": g, "v": v} for g in range(200) for v in ("a", "b", "a", "b")]
    full = functional_dependency(rows, ("k",), "v")
    assert full.confidence == 0.5 and full.violating_groups == 200 and not full.truncated
    bounded = functional_dependency(rows, ("k",), "v", max_groups=10)
    assert bounded.truncated and bounded.rows_untracked == 4 * 190
    assert bounded.confidence == 0.5  # the tracked groups are as unpredictable as the rest
    solid = [{"k": g, "v": g % 3} for g in range(200) for _ in range(4)]
    assert functional_dependency(solid, ("k",), "v", max_groups=10).confidence == 1.0
