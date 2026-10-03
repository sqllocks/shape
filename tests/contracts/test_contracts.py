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


# ---- P1-10: the contracts read the v2 model; P14 and P15


def _v2(columns, rows):
    cols = [
        {
            "arrow_type": "int64",
            "kind": "int",
            "count": rows,
            "null_count": 0,
            "error_models": {},
            **c,
        }
        for c in columns
    ]
    return {
        "schema_version": 2,
        "engine": "t",
        "mode": "exact",
        "tables": {"t": {"name": "t", "rows": rows, "columns": cols}},
    }


def test_unique_uses_the_exact_count_in_exact_mode():
    def rep(distinct):
        col = {"name": "id", "distinct": float(distinct), "distinct_exact": True}
        return evaluate_contract(_v2([col], 30_000), {"columns": {"id": {"unique": True}}})

    assert rep(30_000).passed
    assert not rep(29_999).passed


def test_unique_ids_pass_at_5k_to_30k_rows_on_a_sketch_estimate():
    """P14: an HLL estimate below the row count by less than its error bound is not evidence of
    duplicates (the old check failed unique ids at these sizes)."""
    err = {"cardinality": {"algorithm": "hyperloglog", "exact": False, "relative_error": 0.008125}}
    for rows in (5_000, 12_345, 30_000):
        col = {
            "name": "id",
            "distinct": rows * 0.996,
            "distinct_exact": False,
            "error_models": err,
        }
        rep = evaluate_contract(_v2([col], rows), {"columns": {"id": {"unique": True}}})
        assert rep.passed, rows
    col = {"name": "id", "distinct": 0.9 * 10_000, "distinct_exact": False, "error_models": err}
    bad = evaluate_contract(_v2([col], 10_000), {"columns": {"id": {"unique": True}}})
    assert not bad.passed and bad.violations[0].code == "unique"
    assert bad.violations[0].observed[0] < bad.violations[0].expected


def test_null_rate_of_an_empty_table_is_zero():
    """P15: rows = 0 divided by one before, so a column with nulls recorded looked non-zero."""
    col = {"name": "a", "null_count": 7}
    rep = evaluate_contract(_v2([col], 0), {"columns": {"a": {"nullable_max": 0}}})
    assert rep.passed


def test_contracts_name_the_table_when_there_are_several():
    import pytest

    two = _v2([{"name": "a"}], 3)
    two["tables"]["u"] = {"name": "u", "rows": 1, "columns": []}
    with pytest.raises(ValueError, match="name one of"):
        evaluate_contract(two, {"columns": {"a": {}}})
    assert evaluate_contract(two, {"columns": {"a": {}}}, table="t").passed


def test_a_contract_kind_is_a_v2_kind_or_the_numeric_family():
    model = _v2([{"name": "a", "kind": "int"}, {"name": "b", "kind": "float"}], 1)
    ok = {"columns": {"a": {"kind": "numeric"}, "b": {"kind": "float"}}}
    assert evaluate_contract(model, ok).passed
    bad = evaluate_contract(model, {"columns": {"a": {"kind": "text"}}})
    assert bad.violations[0].code == "type"


# ---- AUD-quality #477: errors that say what is wrong


def test_evaluate_contract_refuses_columns_that_are_not_an_object():
    import pytest

    with pytest.raises(ValueError, match="columns"):
        evaluate_contract(S, {"columns": ["id"]})
    with pytest.raises(ValueError, match="email"):
        evaluate_contract(S, {"columns": {"email": "required"}})


def test_compatibility_names_the_modes():
    import pytest

    with pytest.raises(ValueError, match="backward, forward or full"):
        compatibility(S, S, "sideways")
