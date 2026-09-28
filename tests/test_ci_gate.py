from shape.ci import evaluate_ci


def test_ci_gate():
    a = {
        "rows": 10,
        "columns": {
            "x": {
                "kind": "numeric",
                "null_count": 0,
                "distinct_estimate": 10,
                "mean": 10,
                "variance_population": 1,
                "min": 8,
                "max": 12,
            }
        },
    }
    b = {
        "rows": 10,
        "columns": {
            "x": {
                "kind": "numeric",
                "null_count": 0,
                "distinct_estimate": 10,
                "mean": 10.1,
                "variance_population": 1,
                "min": 8,
                "max": 12,
            }
        },
    }
    assert evaluate_ci(
        a, b, contract={"columns": {"x": {"kind": "numeric"}}}, max_drift=0.2, min_fidelity=0.9
    ).passed
