from shape.generation import ShapePoint, assess_fidelity, interpolate


def test_levels():
    assert (
        assess_fidelity(
            {
                "schema",
                "nullability",
                "univariate",
                "categorical_distribution",
                "missingness_dependencies",
                "dependencies",
                "semantics",
            }
        )
        == "gold"
    )


def test_interp():
    a = ShapePoint(0, {"columns": {"x": {"kind": "numeric", "mean": 0}}})
    b = ShapePoint(10, {"columns": {"x": {"kind": "numeric", "mean": 10}}})
    assert interpolate(a, b, 5)["columns"]["x"]["mean"] == 5
