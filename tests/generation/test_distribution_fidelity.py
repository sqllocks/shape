from shape.generation import categorical_fidelity, quantile_fidelity


def test_dist():
    assert (
        categorical_fidelity({"a": 0.5, "b": 0.5}, ["a", "b"] * 20, 0.01).passed
        and quantile_fidelity(
            {"q25": 1, "q50": 2, "q75": 3}, {"q25": 1, "q50": 2, "q75": 3}, 0.01
        ).passed
    )
