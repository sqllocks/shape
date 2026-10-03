"""The sample payer tables meet the contract and are internally consistent."""

from __future__ import annotations

from shape_healthcare_standards import contract
from shape_healthcare_standards.codes import is_icd10cm, is_icd10pcs, npi_check_digit_ok
from shape_healthcare_standards.common import TableSet
from shape_healthcare_standards.testing import sample_tables


def test_sample_tables_meet_the_contract():
    tables = sample_tables()
    assert set(tables) == set(contract.TABLE_NAMES)
    for name, t in tables.items():
        assert t.schema == contract.contract_schema(name)
        assert contract.check_keys(name, t) == []


def test_sample_references_and_codes_hold():
    ts = TableSet(sample_tables())
    members = ts.index("member", "member_id")
    providers = ts.index("provider", "npi")
    claims = ts.index("medical_claim", "claim_id")
    for c in claims.values():
        assert c["member_id"] in members and c["billing_npi"] in providers
    for ln in ts.rows("medical_claim_line"):
        assert ln["claim_id"] in claims
    assert all(is_icd10cm(d["icd10_code"]) for d in ts.rows("claim_diagnosis"))
    assert all(is_icd10pcs(p["icd10pcs_code"]) for p in ts.rows("claim_procedure"))
    assert all(npi_check_digit_ok(p["npi"]) and p["npi"][0] == "9" for p in providers.values())
    for c in claims.values():  # paid + member responsibility = allowed
        resp = c["member_copay"] + c["member_coinsurance"] + c["member_deductible"]
        assert abs(c["total_paid"] + resp - c["total_allowed"]) < 0.005
