"""shape.check: every rule of contract v1, pass and fail."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pytest

import shape
from shape.contracts.v1 import ContractError


@pytest.fixture()
def prof(orders):
    return shape.profile(orders)


def rules(result, rule=None):
    return [v for v in result.violations if rule is None or v["rule"] == rule]


def test_empty_contract_passes(prof):
    r = shape.check(prof, {})
    assert r.passed and r.violations == []
    assert r.to_dict() == {"passed": True, "violations": []}


def test_full_passing_contract(prof):
    contract = {
        "row_count": {"min": 100, "max": 1000},
        "columns": {
            "order_id": {"dtype": "integer", "nullable": False, "unique": True},
            "email": {"max_null_rate": 0.2, "pattern": "email"},
            "status": {"allowed_values": ["placed", "shipped", "returned"]},
            "amount": {"min": 0, "max": 100000, "distribution": "lognormal"},
        },
        "required_columns": ["order_id", "order_date"],
        "allow_extra_columns": True,
    }
    r = shape.check(prof, contract)
    assert r.passed, r.violations


def test_row_count_min_and_max_fail(prof):
    r = shape.check(prof, {"row_count": {"min": 1000, "max": 10}})
    assert {v["rule"] for v in r.violations} == {"row_count.min", "row_count.max"}
    assert all(v["column"] is None and v["observed"] == 400 for v in r.violations)


def test_dtype_fail(prof):
    r = shape.check(prof, {"columns": {"order_id": {"dtype": "string"}}})
    assert r.violations == [
        {"column": "order_id", "rule": "dtype", "expected": "string", "observed": "integer"}
    ]


def test_nullable_false_fail_and_pass(prof):
    r = shape.check(prof, {"columns": {"email": {"nullable": False}}})
    assert rules(r, "nullable") and r.violations[0]["observed"]["null_count"] > 0
    assert shape.check(prof, {"columns": {"order_id": {"nullable": False}}}).passed
    assert shape.check(prof, {"columns": {"email": {"nullable": True}}}).passed


def test_unique_fail_and_pass(prof):
    r = shape.check(prof, {"columns": {"customer_id": {"unique": True}}})
    assert rules(r, "unique")
    assert shape.check(prof, {"columns": {"order_id": {"unique": True}}}).passed


def test_unique_uses_exact_distinct_count():
    dup = shape.profile(pa.table({"k": list(range(999)) + [0]}))
    r = shape.check(dup, {"columns": {"k": {"unique": True}}})
    assert not r.passed
    assert r.violations[0]["observed"]["cardinality"] == 999


def test_max_null_rate(prof):
    assert not shape.check(prof, {"columns": {"email": {"max_null_rate": 0.0}}}).passed
    assert shape.check(prof, {"columns": {"email": {"max_null_rate": 0.5}}}).passed


def test_pattern(prof):
    r = shape.check(prof, {"columns": {"email": {"pattern": "phone"}}})
    assert r.violations == [
        {"column": "email", "rule": "pattern", "expected": "phone", "observed": "email"}
    ]


def test_allowed_values_pass_fail_and_new_value(orders):
    prof = shape.profile(orders)
    assert shape.check(
        prof, {"columns": {"status": {"allowed_values": ["placed", "shipped", "returned", "x"]}}}
    ).passed
    r = shape.check(prof, {"columns": {"status": {"allowed_values": ["placed", "shipped"]}}})
    assert r.violations[0]["observed"] == {"unexpected_values": ["returned"]}


def test_allowed_values_with_numbers_and_bools():
    p = shape.profile(pa.table({"n": [1, 2, 1, 2] * 5, "b": [True, False] * 10}))
    assert shape.check(p, {"columns": {"n": {"allowed_values": [1, 2]}}}).passed
    assert not shape.check(p, {"columns": {"n": {"allowed_values": [1]}}}).passed
    assert shape.check(p, {"columns": {"b": {"allowed_values": [True, False]}}}).passed


def test_allowed_values_high_cardinality_fails_by_count():
    p = shape.profile(pa.table({"s": [f"v{i}" for i in range(2000)]}))
    r = shape.check(p, {"columns": {"s": {"allowed_values": ["v1", "v2"]}}})
    assert not r.passed


def test_min_max(prof):
    assert shape.check(prof, {"columns": {"amount": {"min": 0, "max": 1e9}}}).passed
    r = shape.check(prof, {"columns": {"amount": {"min": 1e6, "max": 1}}})
    assert {v["rule"] for v in r.violations} == {"min", "max"}
    # min/max on a string column compare as strings; mixed types are a violation
    assert not shape.check(prof, {"columns": {"status": {"min": 5}}}).passed
    assert shape.check(prof, {"columns": {"status": {"min": "a", "max": "z"}}}).passed


def test_distribution(prof):
    r = shape.check(prof, {"columns": {"amount": {"distribution": "normal"}}})
    assert r.violations[0]["rule"] == "distribution"
    assert r.violations[0]["observed"] == "lognormal"


def test_required_columns(prof):
    r = shape.check(prof, {"required_columns": ["order_id", "nope"]})
    assert r.violations == [
        {"column": "nope", "rule": "required_column", "expected": "present", "observed": "missing"}
    ]


def test_rule_on_missing_column(prof):
    r = shape.check(prof, {"columns": {"ghost": {"dtype": "integer"}}})
    assert r.violations[0]["rule"] == "column_exists"
    both = shape.check(
        prof, {"columns": {"ghost": {"dtype": "integer"}}, "required_columns": ["ghost"]}
    )
    assert len(both.violations) == 1


def test_allow_extra_columns_false(prof):
    r = shape.check(prof, {"columns": {"order_id": {}}, "allow_extra_columns": False})
    extra = {v["column"] for v in r.violations if v["rule"] == "extra_column"}
    assert extra == set(prof.to_dict()["columns"]) - {"order_id"}
    assert shape.check(prof, {"columns": {"order_id": {}}, "allow_extra_columns": True}).passed


def test_contract_from_json_path(tmp_path: Path, prof):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"row_count": {"min": 1}}))
    assert shape.check(prof, path).passed
    assert shape.check(prof, str(path)).passed


@pytest.mark.parametrize(
    "contract",
    [
        {"colums": {}},
        {"columns": {"a": {"dtypes": "integer"}}},
        {"row_count": {"minimum": 1}},
        {"columns": []},
        {"required_columns": "a"},
        {"columns": {"a": {"allowed_values": "x"}}},
        [],
    ],
)
def test_malformed_contract_raises(prof, contract):
    with pytest.raises(ContractError):
        shape.check(prof, contract)


def test_invalid_json_file_raises(tmp_path: Path, prof):
    path = tmp_path / "bad.json"
    path.write_text("{nope")
    with pytest.raises(ContractError):
        shape.check(prof, path)


def test_multi_table_contract(orders, customers):
    p = shape.profile({"orders": orders, "customer": customers})
    r = shape.check(
        p,
        {
            "tables": {
                "orders": {"row_count": {"min": 1000}},
                "customer": {"required_columns": ["name"]},
            }
        },
    )
    assert [v["rule"] for v in r.violations] == ["orders:row_count.min"]
    with pytest.raises(ContractError):
        shape.check(p, {"row_count": {"min": 1}})
    missing = shape.check(p, {"tables": {"ghost": {}}})
    assert missing.violations[0]["rule"] == "table_exists"
    col = shape.check(p, {"tables": {"customer": {"columns": {"name": {"dtype": "integer"}}}}})
    assert col.violations[0]["column"] == "customer.name"
