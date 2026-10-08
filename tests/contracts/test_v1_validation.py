"""Malformed contract values are refused with ContractError, never crash or pass unchecked."""

from __future__ import annotations

import pyarrow as pa
import pytest

import shape
from shape.contracts.v1 import ContractError, check


@pytest.fixture(scope="module")
def prof():
    return shape.profile(pa.table({"id": [1, 1, None], "flag": [True, False, True]}))


@pytest.fixture(scope="module")
def dataset():
    return shape.profile({"a": pa.table({"id": [1, 2]}), "b": pa.table({"id": [1, 1]})})


# #462: a rule value that is not a number crashed shape.check with TypeError
@pytest.mark.parametrize(
    "contract",
    [
        {"row_count": {"min": "10"}},
        {"row_count": {"max": None}},
        {"row_count": {"min": True}},
        {"row_count": {"min": float("nan")}},
        {"columns": {"id": {"max_null_rate": "0.1"}}},
        {"columns": {"id": {"max_null_rate": float("nan")}}},
        {"columns": {"id": {"max_null_rate": 2}}},
    ],
)
def test_a_rule_that_is_not_a_number_is_a_contract_error(prof, contract):
    with pytest.raises(ContractError):
        check(prof, contract)


def test_well_formed_number_rules_still_work(prof):
    assert check(prof, {"row_count": {"min": 1, "max": 3.0}}).passed
    assert not check(prof, {"columns": {"id": {"max_null_rate": 0}}}).passed


# #463: a flag that is not a JSON boolean was silently not checked
@pytest.mark.parametrize(
    "contract",
    [
        {"columns": {"id": {"nullable": "false"}}},
        {"columns": {"id": {"nullable": 0}}},
        {"columns": {"id": {"unique": "true"}}},
        {"columns": {"id": {"unique": 1}}},
        {"allow_extra_columns": "false"},
    ],
)
def test_a_flag_that_is_not_a_boolean_is_a_contract_error(prof, contract):
    with pytest.raises(ContractError):
        check(prof, contract)


def test_boolean_flags_still_work(prof):
    assert not check(prof, {"columns": {"id": {"nullable": False}}}).passed
    assert not check(prof, {"columns": {"id": {"unique": True}}}).passed
    assert check(prof, {"columns": {"id": {"nullable": True, "unique": False}}}).passed
    assert not check(prof, {"columns": {"id": {}}, "allow_extra_columns": False}).passed


# #464: a dataset contract ignored table rules next to "tables"; a bad sub-contract crashed
@pytest.mark.parametrize(
    "contract",
    [
        {"tables": {"a": {}}, "row_count": {"min": 1000}},
        {"tables": {"a": {}}, "columns": {"id": {"unique": True}}},
        {"tables": {"a": {}}, "required_columns": ["zz"]},
        {"tables": {"a": 5}},
        {"tables": {"a": ["x"]}},
        {"tables": {"a": {"tables": {"x": {}}}}},
        {"tables": []},
    ],
)
def test_a_misplaced_dataset_rule_is_a_contract_error(dataset, contract):
    with pytest.raises(ContractError):
        check(dataset, contract)


def test_a_dataset_contract_with_a_drift_policy_still_works(dataset):
    contract = {"tables": {"b": {"columns": {"id": {"unique": True}}}}, "drift": {"ignore": []}}
    result = check(dataset, contract)
    assert not result.passed and result.violations[0]["column"] == "b.id"


def test_a_dataset_contract_that_declares_its_format_still_works(dataset):
    """INT-18: the declaration W1-01 stamps on every contract (format, version, shape_version,
    min_shape_version) is not a table rule beside `tables` (AUD-quality #464)."""
    from shape import compat

    contract = compat.stamp("contract", {"tables": {"b": {"columns": {"id": {"unique": True}}}}})
    assert set(compat.BOOKKEEPING_KEYS) <= set(contract)
    result = check(dataset, contract)
    assert not result.passed and result.violations[0]["column"] == "b.id"
    with pytest.raises(ContractError):  # a real rule beside it still is
        check(dataset, {**contract, "row_count": {"min": 1}})
