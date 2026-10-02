"""PF-06b: a contract and the profile it is checked against must describe the same tables.

A ``{"tables": {...}}`` contract checked against a single-table profile used to pass without
testing a rule, so a pipeline passed on damaged data.
"""

from __future__ import annotations

import pyarrow as pa
import pytest

import shape
from shape.contracts.v1 import ContractError, check

ORDERS = pa.table({"id": [1, 2, 3], "status": ["a", "b", "a"]})
CUSTOMER = pa.table({"id": [1, 2], "name": ["x", "y"]})
CONTRACT = {
    "tables": {
        "orders": {"row_count": {"min": 3}, "columns": {"id": {"dtype": "integer"}}},
        "customer": {"row_count": {"min": 2}},
    }
}


def test_a_tables_contract_against_a_single_table_profile_is_an_error():
    single = shape.profile(ORDERS, name="orders")
    with pytest.raises(ContractError, match="single table"):
        check(single, CONTRACT)
    # Even a contract that no rule of could fail: the mismatch is the error, not the rules.
    with pytest.raises(ContractError, match="'tables'"):
        check(single, {"tables": {}})


def test_a_single_table_contract_against_a_single_table_profile_is_unchanged():
    single = shape.profile(ORDERS, name="orders")
    assert check(single, {"row_count": {"min": 3}}).passed
    assert not check(single, {"row_count": {"min": 4}}).passed


def test_matching_tables_pass():
    both = shape.profile({"orders": ORDERS, "customer": CUSTOMER})
    assert check(both, CONTRACT).passed


def test_a_contract_table_missing_from_the_profile_fails():
    only_orders = shape.profile({"orders": ORDERS, "other": CUSTOMER})
    result = check(only_orders, CONTRACT)
    assert not result.passed
    missing = {
        "column": None,
        "rule": "table_exists",
        "expected": "customer",
        "observed": "missing",
    }
    assert missing in result.violations


def test_a_profile_table_the_contract_does_not_name_is_not_checked():
    # Contract v1 is final for 1.0 and every rule is optional: a contract may check a subset of
    # a dataset's tables (owner decision, 2026-10-02). The vacuous pass guarded here is the
    # single-table case and a named table that is missing.
    extra = shape.profile({"orders": ORDERS, "customer": CUSTOMER, "audit": CUSTOMER})
    assert check(extra, CONTRACT).passed


def test_a_dataset_profile_against_a_single_table_contract_is_still_an_error():
    both = shape.profile({"orders": ORDERS, "customer": CUSTOMER})
    with pytest.raises(ContractError, match="several tables"):
        check(both, {"row_count": {"min": 1}})
