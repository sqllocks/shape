from shape.profile.bounded_dependencies import bounded_functional_dependency


def test_bounded_fd():
    rows = ({"x": i % 100, "y": (i % 100) * 2} for i in range(10000))
    r = bounded_functional_dependency(rows, ("x",), "y", 500)
    assert (
        r.rows_seen == 10000
        and r.rows_sampled == 500
        and r.confidence == 1.0
        and 0 < r.sampling_rate < 1
    )
