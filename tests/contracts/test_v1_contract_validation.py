"""Contract v1: a malformed rule or a misplaced section is a ``ContractError``, never a pass.

Two defects, both reached through ``shape.check``:

- Rule values were not type-checked. ``nullable`` and ``unique`` were compared by identity with
  ``True``/``False``, so ``"nullable": "false"`` or ``"unique": "true"`` tested nothing and the
  check passed over nulls and duplicates; ``max_null_rate: 2`` (or ``True``, NaN, infinity)
  passed any column; a text ``row_count`` bound crashed with ``TypeError``.
- Sections were ignored: a dataset contract applied its ``tables`` but never the table rules
  beside them, and a table's contract that was not an object, or held its own ``tables``, was
  either a ``TypeError`` or silently skipped.

A malformed contract is rejected before any profile is evaluated, and the message names the rule
and the column or table it belongs to.
"""

from __future__ import annotations

import math
from typing import Any

import pyarrow as pa
import pytest

import shape
from shape.contracts.v1 import ContractError

# One null and one duplicate: every nullable / unique / max_null_rate rule has evidence to fail.
DIRTY = pa.table({"id": [1, 1, None], "name": ["a", "b", "c"]})
CLEAN = pa.table({"id": [1, 2], "name": ["x", "y"]})


@pytest.fixture(scope="module")
def single() -> Any:
    return shape.profile(DIRTY, name="orders")


@pytest.fixture(scope="module")
def dataset() -> Any:
    return shape.profile({"orders": DIRTY, "customer": CLEAN})


# --- malformed rule values ------------------------------------------------------------------

NOT_BOOLEANS = ["false", "true", 0, 1, None, [], {}]
NOT_RATES = [2, -0.1, 1.0001, "0.1", True, False, None, math.nan, math.inf, -math.inf, [0.1]]
NOT_COUNTS = ["10", True, False, None, math.nan, math.inf, -math.inf, [10], {"n": 10}]
UNCOMPARABLE_BOUNDS = [True, None, math.nan, [1], {"v": 1}, "a"]


@pytest.mark.parametrize("rule", ["nullable", "unique"])
@pytest.mark.parametrize("value", NOT_BOOLEANS, ids=repr)
def test_a_boolean_column_rule_that_is_not_a_boolean_is_rejected(single, rule, value):
    with pytest.raises(ContractError, match=rf"{rule}.*'id'.*true or false"):
        shape.check(single, {"columns": {"id": {rule: value}}})


@pytest.mark.parametrize("value", NOT_BOOLEANS, ids=repr)
def test_allow_extra_columns_that_is_not_a_boolean_is_rejected(single, value):
    with pytest.raises(ContractError, match=r"allow_extra_columns.*true or false"):
        shape.check(single, {"columns": {"id": {}}, "allow_extra_columns": value})


@pytest.mark.parametrize("value", NOT_RATES, ids=repr)
def test_max_null_rate_must_be_a_finite_number_from_0_to_1(single, value):
    with pytest.raises(ContractError, match=r"max_null_rate.*'id'.*from 0 to 1"):
        shape.check(single, {"columns": {"id": {"max_null_rate": value}}})


@pytest.mark.parametrize("rule", ["min_true_rate", "max_true_rate"])
@pytest.mark.parametrize("value", NOT_RATES, ids=repr)
def test_true_rates_must_be_finite_numbers_from_0_to_1(single, rule, value):
    with pytest.raises(ContractError, match=rf"{rule}.*'id'.*from 0 to 1"):
        shape.check(single, {"columns": {"id": {rule: value}}})


@pytest.mark.parametrize("bound", ["min", "max"])
@pytest.mark.parametrize("value", NOT_COUNTS, ids=repr)
def test_row_count_bounds_must_be_finite_numbers(single, bound, value):
    with pytest.raises(ContractError, match=rf"row_count\.{bound}.*number"):
        shape.check(single, {"row_count": {bound: value}})


@pytest.mark.parametrize("bound", ["min", "max"])
@pytest.mark.parametrize("value", UNCOMPARABLE_BOUNDS, ids=repr)
def test_a_column_bound_the_profile_cannot_compare_with_is_a_violation(single, bound, value):
    # Non-numeric bounds are valid contract content (``shape contract emit`` reports them as not
    # expressed); ``check`` never lets one pass untested.
    result = shape.check(single, {"columns": {"id": {bound: value}}})
    assert not result.passed
    assert [(v["column"], v["rule"]) for v in result.violations] == [("id", bound)]


@pytest.mark.parametrize(
    ("rules", "message"),
    [
        ({"allowed_values": "a"}, r"allowed_values for column 'id'"),
        ({"no_placeholder": "yes"}, r"no_placeholder for column 'id'"),
        ({"no_placeholder": {"max_share": math.nan}}, r"no_placeholder.max_share.*'id'"),
        ({"no_placeholder": {"max_share": True}}, r"no_placeholder.max_share.*'id'"),
        ({"bogus": 1}, r"unknown rules for column 'id'"),
    ],
)
def test_other_malformed_column_rules_are_still_rejected(single, rules, message):
    with pytest.raises(ContractError, match=message):
        shape.check(single, {"columns": {"id": rules}})


@pytest.mark.parametrize(
    ("contract", "message"),
    [
        ({"max_implausible_rate": math.inf}, r"max_implausible_rate"),
        ({"max_implausible_rate": True}, r"max_implausible_rate"),
        ({"fd": [{"determinant": "id", "dependent": "name", "min_confidence": math.inf}]}, "fd"),
        ({"required_columns": "id"}, r"required_columns"),
    ],
)
def test_malformed_table_rules_are_rejected(single, contract, message):
    with pytest.raises(ContractError, match=message):
        shape.check(single, contract)


def test_a_malformed_rule_is_rejected_even_when_its_column_is_missing(single):
    # Validation comes before evaluation: a rule is malformed whatever the profile holds.
    with pytest.raises(ContractError, match=r"nullable for column 'absent'"):
        shape.check(single, {"columns": {"absent": {"nullable": "false"}}})


def test_a_malformed_rule_in_a_table_contract_names_the_table(dataset):
    contract = {"tables": {"orders": {"columns": {"id": {"unique": "true"}}}}}
    with pytest.raises(ContractError, match=r"table 'orders'.*unique for column 'id'"):
        shape.check(dataset, contract)


def test_a_malformed_contract_never_returns_a_result(single, dataset):
    malformed = [
        (single, {"columns": {"id": {"nullable": "false"}}}),
        (single, {"columns": {"id": {"unique": "true"}}}),
        (single, {"columns": {"id": {"max_null_rate": 2}}}),
        (single, {"allow_extra_columns": "false"}),
        (single, {"row_count": {"min": True}}),
        (dataset, {"tables": {"orders": {"columns": {"id": {"nullable": "false"}}}}}),
        (dataset, {"tables": {"orders": {"columns": {"id": {"max_null_rate": 2}}}}}),
    ]
    for profile, contract in malformed:
        with pytest.raises(ContractError):
            shape.check(profile, contract)


# --- valid rules still find what they test ---------------------------------------------------


def test_valid_boolean_and_rate_rules_still_detect_violations(single):
    result = shape.check(
        single, {"columns": {"id": {"nullable": False, "unique": True, "max_null_rate": 0.1}}}
    )
    assert not result.passed
    assert {v["rule"] for v in result.violations} == {"nullable", "unique", "max_null_rate"}


def test_valid_rules_that_hold_pass(single):
    contract = {
        "row_count": {"min": 3, "max": 3.0},
        "columns": {
            "id": {"nullable": True, "unique": False, "max_null_rate": 1, "min": 1, "max": 1.0},
            "name": {"nullable": False, "unique": True, "max_null_rate": 0, "min": "a"},
        },
        "allow_extra_columns": False,
    }
    assert shape.check(single, contract).passed


def test_allow_extra_columns_false_still_detects_an_extra_column(single):
    result = shape.check(single, {"columns": {"id": {}}, "allow_extra_columns": False})
    assert [v["rule"] for v in result.violations] == ["extra_column"]


def test_row_count_bounds_still_detect_violations(single):
    result = shape.check(single, {"row_count": {"min": 4, "max": 2}})
    assert [v["rule"] for v in result.violations] == ["row_count.min", "row_count.max"]


# --- single-table and dataset contracts ------------------------------------------------------


def test_an_empty_contract_passes_a_single_table(single):
    assert shape.check(single, {}).passed


def test_an_empty_tables_object_passes_a_dataset(dataset):
    assert shape.check(dataset, {"tables": {}}).passed
    assert shape.check(dataset, {"tables": {"orders": {}, "customer": {}}}).passed


def test_a_dataset_contract_checks_each_table(dataset):
    contract = {
        "tables": {
            "orders": {"columns": {"id": {"nullable": False, "max_null_rate": 0.1}}},
            "customer": {"columns": {"id": {"nullable": False, "unique": True}}},
        }
    }
    result = shape.check(dataset, contract)
    assert not result.passed
    assert sorted((v["column"], v["rule"]) for v in result.violations) == [
        ("orders.id", "max_null_rate"),
        ("orders.id", "nullable"),
    ]


def test_a_tables_contract_against_a_single_table_profile_is_rejected(single):
    with pytest.raises(ContractError, match="single table"):
        shape.check(single, {"tables": {"orders": {"row_count": {"min": 1000}}}})


def test_a_single_table_contract_against_a_dataset_profile_is_rejected(dataset):
    with pytest.raises(ContractError, match="several tables"):
        shape.check(dataset, {"row_count": {"min": 1000}})


@pytest.mark.parametrize(
    "beside",
    [
        {"row_count": {"min": 1000}},
        {"columns": {"id": {"nullable": False}}},
        {"required_columns": ["missing"]},
        {"allow_extra_columns": False},
        {"max_implausible_rate": 0},
        {"fd": [{"determinant": "id", "dependent": "name", "min_confidence": 1}]},
    ],
    ids=lambda b: next(iter(b)),
)
def test_table_rules_beside_tables_are_rejected_not_ignored(dataset, beside):
    key = next(iter(beside))
    with pytest.raises(ContractError, match=rf"'tables'.*{key}"):
        shape.check(dataset, {"tables": {"orders": {}}, **beside})


def test_table_rules_beside_tables_are_rejected_for_a_single_table_profile_too(single):
    with pytest.raises(ContractError, match="'tables'"):
        shape.check(single, {"tables": {"orders": {}}, "row_count": {"min": 1000}})


def test_keys_that_belong_beside_tables_are_still_accepted(dataset):
    contract = {
        "format": "shape-contract",
        "version": 1,
        "x_owner": "data team",
        "drift": {},
        "tables": {"customer": {"row_count": {"min": 2}}},
    }
    assert shape.check(dataset, contract).passed


@pytest.mark.parametrize(
    ("tables", "message"),
    [
        (["orders"], r"'tables' must be an object"),
        ("orders", r"'tables' must be an object"),
        ({"orders": 5}, r"contract of table 'orders' must be an object"),
        ({"orders": None}, r"contract of table 'orders' must be an object"),
        ({"orders": ["id"]}, r"contract of table 'orders' must be an object"),
        ({1: {}}, r"table names"),
        ({"orders": {"tables": {"x": {}}}}, r"table 'orders'.*cannot hold 'tables'"),
        ({"orders": {"bogus": 1}}, r"table 'orders'.*unknown contract keys"),
        ({"orders": {"columns": []}}, r"table 'orders'.*'columns' must be an object"),
        ({"orders": {"row_count": {"min": "1"}}}, r"table 'orders'.*row_count\.min"),
    ],
    ids=repr,
)
def test_malformed_table_mappings_and_sub_contracts_are_rejected(dataset, tables, message):
    with pytest.raises(ContractError, match=message):
        shape.check(dataset, {"tables": tables})


def test_every_table_contract_is_validated_before_any_is_evaluated(dataset):
    # The first table's contract is fine and fails its rules; the second is malformed. The
    # malformed contract is the answer, not a result built from the first table alone.
    contract = {
        "tables": {
            "orders": {"row_count": {"min": 1000}},
            "customer": {"columns": {"id": {"nullable": "false"}}},
        }
    }
    with pytest.raises(ContractError, match=r"table 'customer'"):
        shape.check(dataset, contract)


def test_an_incompatible_contract_never_passes_because_rules_were_ignored(single, dataset):
    # Each contract below holds a rule that the profile breaks. Before the fix every one of them
    # either passed (the rule was skipped) or crashed with an unrelated error.
    incompatible = [
        (dataset, {"tables": {"orders": {}}, "row_count": {"min": 1000}}),
        (dataset, {"tables": {"orders": {}}, "columns": {"id": {"nullable": False}}}),
        (dataset, {"tables": {"orders": {"tables": {"customer": {"row_count": {"min": 9}}}}}}),
        (dataset, {"tables": {"orders": 5}}),
        (single, {"tables": {"orders": {"row_count": {"min": 1000}}}}),
        (single, {"tables": {}, "row_count": {"min": 1000}}),
    ]
    for profile, contract in incompatible:
        with pytest.raises(ContractError):
            shape.check(profile, contract)


def test_an_integer_bound_too_large_for_a_float_is_checked_not_a_crash(single):
    """A contract integer beyond the float range (the nightly fuzzer, Nightly run 37233890609:
    ``OverflowError: int too large to convert to float``) is a finite number: it is checked
    against the profile like any other bound."""
    huge = 10**400
    result = shape.check(single, {"row_count": {"min": huge}})
    assert [v["rule"] for v in result.violations] == ["row_count.min"]
    assert shape.check(single, {"row_count": {"max": huge}}).violations == []
    with pytest.raises(ContractError, match="max_null_rate"):
        shape.check(single, {"columns": {"id": {"max_null_rate": huge}}})
