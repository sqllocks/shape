"""Synthetic identifiers can never belong to a real person."""

from __future__ import annotations

import numpy as np
from shape_domains.healthcare_payer import identifiers as ids


def test_npi_check_digit_matches_the_published_example():
    # the CMS NPI check-digit worked example
    assert ids.npi_is_valid("1234567893")
    assert not ids.npi_is_valid("1234567890")
    assert not ids.npi_is_never_assigned("1234567893")  # a leading 1 can be a real NPI


def test_synthetic_npis_are_valid_unique_and_never_assigned():
    source = ids.SyntheticNpis()
    values = [source.npi(n) for n in range(5000)]
    assert len(set(values)) == len(values)
    assert all(
        ids.npi_is_valid(v) and v.startswith("99") and ids.npi_is_never_assigned(v) for v in values
    )


def test_ssn_area_is_never_issued():
    rng = np.random.default_rng(1)
    for _ in range(500):
        v = ids.ssn(rng)
        assert ids.is_never_issued_ssn(v)
        assert v[4:6] != "00" and v[7:] != "0000"


def test_email_and_member_id_formats():
    assert ids.email("Ann", "Lee", 7).endswith(("@example.com", "@example.org", "@example.net"))
    assert ids.member_id(1).startswith("SYN") and ids.subscriber_id(1).startswith("SYS")
    assert ids.pharmacy_ncpdp_id(1).startswith("9")


def test_every_identifier_in_the_generated_tables_is_synthetic(data):
    t = data.tables
    for r in t["member"].to_pylist():
        assert r["member_id"].startswith("SYN") and r["subscriber_id"].startswith("SYS")
        assert ids.is_never_issued_ssn(r["ssn"])
        assert r["email"].endswith(("@example.com", "@example.org", "@example.net"))
        assert len(r["phone"]) == 10 and r["phone"][3:6] == "555" and r["phone"][6:8] == "01"
    npis = set(t["provider"].column("npi").to_pylist())
    assert all(ids.npi_is_never_assigned(n) for n in npis)
    assert len(npis) == t["provider"].num_rows
    for col in ("billing_npi", "rendering_npi", "attending_npi", "facility_npi"):
        used = {v for v in t["medical_claim"].column(col).to_pylist() if v}
        assert used <= npis
    assert {v for v in t["pharmacy_claim"].column("prescriber_npi").to_pylist()} <= npis
    assert {v for v in t["pharmacy_claim"].column("pharmacy_npi").to_pylist()} <= npis
