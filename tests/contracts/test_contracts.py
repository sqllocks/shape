from shape.contracts import compatibility, evaluate_contract

S = {
    "rows": 100,
    "columns": {
        "id": {"kind": "numeric", "null_count": 0, "distinct_estimate": 100, "min": 1, "max": 100},
        "email": {"kind": "text", "null_count": 1, "distinct_estimate": 90},
    },
}


def test_contract_pass_fail():
    r = evaluate_contract(
        S,
        {
            "columns": {
                "id": {"kind": "numeric", "unique": True, "min": 1},
                "email": {"nullable_max": 0.02},
            }
        },
    )
    assert r.passed
    r = evaluate_contract(S, {"columns": {"email": {"nullable_max": 0}}})
    assert not r.passed and r.violations[0].code == "null_rate"


def test_compatibility_modes():
    a = {"columns": {"x": {"kind": "numeric"}}}
    b = {"columns": {"x": {"kind": "numeric"}, "y": {"kind": "text"}}}
    assert compatibility(a, b, "backward").compatible
    assert not compatibility(a, b, "forward").compatible
    assert not compatibility(a, b, "full").compatible
