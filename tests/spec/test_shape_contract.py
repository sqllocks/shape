from shape.spec import FieldContract, ShapeContract


def test_contract():
    c = ShapeContract(
        "customer", fields=(FieldContract("id", "integer", False, sensitivity="CONFIDENTIAL"),)
    ).validate()
    assert ShapeContract.from_json(c.to_json()) == c
