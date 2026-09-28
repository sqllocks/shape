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
