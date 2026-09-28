from shape.generation.fidelity import certify_shapes, plan_reconstruction


def test_fidelity_and_planner():
    a = {
        "rows": 100,
        "columns": {
            "x": {
                "kind": "numeric",
                "null_count": 0,
                "distinct_estimate": 100,
                "mean": 5,
                "variance_population": 4,
                "min": 0,
                "max": 10,
            }
        },
    }
    b = {
        "rows": 100,
        "columns": {
            "x": {
                "kind": "numeric",
                "null_count": 0,
                "distinct_estimate": 100,
                "mean": 5.1,
                "variance_population": 4.1,
                "min": 0,
                "max": 10,
            }
        },
    }
    c = certify_shapes(a, b)
    assert c.score > 0.95 and c.to_dict()["dimensions"]
    p = plan_reconstruction(a)
    assert p.executable and p.items
