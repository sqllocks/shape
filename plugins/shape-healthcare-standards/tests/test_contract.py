"""The input table contract: coercion, required columns, key checks."""

from __future__ import annotations

import datetime as dt

import pyarrow as pa
import pytest
from shape_healthcare_standards import contract
from shape_healthcare_standards.codes import is_icd10cm, is_icd10pcs, luhn_npi, npi_check_digit_ok


def test_every_table_has_a_schema_and_unique_column_names():
    for name, cols in contract.CONTRACT.items():
        names = [c.name for c in cols]
        assert len(names) == len(set(names)), name
        assert contract.contract_schema(name).names == names
        for key in contract.KEYS.get(name, ()):
            assert key in names


def test_coerce_adds_optional_drops_extra_and_casts():
    t = pa.table(
        {
            "member_id": pa.array(["M1"], pa.large_string()),
            "subscriber_id": ["M1"],
            "relationship_code": ["18"],
            "first_name": ["A"],
            "last_name": ["B"],
            "birth_date": pa.array([dt.datetime(1980, 1, 2, 3)], pa.timestamp("us")),
            "sex": ["F"],
            "extra": [1],
        }
    )
    out = contract.coerce("member", t)
    assert out.schema == contract.contract_schema("member")
    assert out.column("birth_date").to_pylist() == [dt.date(1980, 1, 2)]
    assert out.column("city").to_pylist() == [None]
    assert "extra" not in out.column_names


def test_coerce_rejects_missing_required_and_bad_types():
    with pytest.raises(contract.ContractError, match="required"):
        contract.coerce("member", pa.table({"member_id": ["M1"]}))
    bad = pa.table(
        {
            "claim_id": ["c"],
            "line_number": ["x"],
            "service_date": [dt.date(2024, 1, 1)],
            "billed_amount": [1.0],
        }
    )
    with pytest.raises(contract.ContractError, match="line_number"):
        contract.coerce("medical_claim_line", bad)


def test_money_accepts_decimals_and_ints():
    t = pa.table(
        {
            "claim_id": ["c"],
            "line_number": [1],
            "service_date": [dt.date(2024, 1, 1)],
            "billed_amount": pa.array([12], pa.int32()),
        }
    )
    assert contract.coerce("medical_claim_line", t).column("billed_amount").to_pylist() == [12.0]


def test_key_check_finds_duplicates_and_nulls():
    dup = contract.coerce("provider", pa.table({"npi": ["1", "1"], "entity_type": ["1", "1"]}))
    assert contract.check_keys("provider", dup)
    nul = contract.coerce("provider", pa.table({"npi": ["1", None], "entity_type": ["1", "1"]}))
    assert contract.check_keys("provider", nul)
    ok = contract.coerce("provider", pa.table({"npi": ["1", "2"], "entity_type": ["1", "1"]}))
    assert not contract.check_keys("provider", ok)


def test_code_shapes():
    assert is_icd10cm("E1165") and is_icd10cm("S72001A") and not is_icd10cm("E11.65")
    assert is_icd10pcs("0FT44ZZ") and not is_icd10pcs("0FT44IZ")
    npi = luhn_npi("123456789")
    bad = npi[:-1] + str((int(npi[-1]) + 1) % 10)
    assert npi_check_digit_ok(npi) and not npi_check_digit_ok(bad)
