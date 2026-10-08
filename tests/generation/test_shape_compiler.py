import numpy as np

from shape.capture import capture_columns
from shape.generation import generate_from_shape, generate_relational


def test_generate_whole_shape_marginals_and_correlation():
    n = 50000
    x = np.arange(n, dtype=float)
    y = 2 * x + np.random.default_rng(1).normal(0, 100, n)
    s = capture_columns({"x": x, "y": y})
    s["relationships"] = {"correlations": [{"source": "x", "target": "y", "rho": 0.99}]}
    data, report = generate_from_shape(s, n, 7)
    assert (
        len(data["x"]) == n
        and np.corrcoef(data["x"].astype(float), data["y"].astype(float))[0, 1] > 0.97
    )
    assert not report.degraded


def test_relational_generation_enforces_fk():
    parent = {
        "rows": 100,
        "columns": {
            "id": {
                "kind": "numeric",
                "count": 100,
                "mean": 49.5,
                "variance_population": 833.25,
                "min": 0,
                "max": 99,
            }
        },
    }
    child = {
        "rows": 1000,
        "columns": {
            "amount": {
                "kind": "numeric",
                "count": 1000,
                "mean": 10,
                "variance_population": 4,
                "min": 0,
                "max": 20,
            }
        },
    }
    out = generate_relational(
        {"p": parent, "c": child},
        {"p": 100, "c": 1000},
        [{"parent": "p", "child": "c", "parent_key": "id", "child_fk": "pid"}],
        3,
    )
    assert set(out["c"]["pid"]).issubset(set(out["p"]["id"]))
