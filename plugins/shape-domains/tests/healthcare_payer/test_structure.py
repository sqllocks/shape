"""Members, eligibility, providers, plans and the claim lifecycle."""

from __future__ import annotations

from collections import Counter
from datetime import date

from shape_domains.healthcare_payer import generate as gen
from shape_domains.healthcare_payer.reference import CARC, RARC, SPECIALTY


def test_every_table_is_present_with_rows(data):
    assert tuple(data.tables) == gen.TABLE_NAMES
    for name, table in data.tables.items():
        assert table.num_rows > 0, name


def test_subscriber_and_dependent_structure(data):
    members = data.tables["member"].to_pylist()
    by_sub: dict[str, list[dict]] = {}
    for m in members:
        by_sub.setdefault(m["subscriber_id"], []).append(m)
    assert Counter(m["relationship_code"] for m in members).keys() >= {"18", "01", "19"}
    for sub, group in by_sub.items():
        assert sum(1 for m in group if m["relationship_code"] == "18") == 1, sub
        assert len({m["member_suffix"] for m in group}) == len(group), (
            sub
        )  # unique suffix per subscriber
        assert len({m["household_id"] for m in group}) == 1
        for m in group:
            if m["relationship_code"] == "19":
                assert m["line_of_business"] in ("commercial", "medicaid")
    kids = [
        m
        for m in members
        if m["relationship_code"] == "19" and m["line_of_business"] == "commercial"
    ]
    assert kids and all((date(2024, 12, 31) - m["birth_date"]).days / 365.25 < 26.5 for m in kids)


def test_eligibility_spans_have_gaps_and_reenrollment(data):
    spans = data.tables["eligibility"].to_pylist()
    by_member: dict[str, list[dict]] = {}
    for s in spans:
        by_member.setdefault(s["member_id"], []).append(s)
    gaps = [s["gap_before_days"] for s in spans if s["gap_before_days"] is not None]
    assert gaps and min(gaps) >= 0 and max(gaps) > 30
    assert sum(1 for v in by_member.values() if len(v) > 1) > 20
    for v in by_member.values():
        v.sort(key=lambda s: s["coverage_start"])
        for a, b in zip(v, v[1:], strict=False):
            assert a["coverage_end"] is not None and a["coverage_end"] < b["coverage_start"]
    reasons = Counter(s["termination_reason"] for s in spans if s["coverage_end"])
    assert {"disenrolled", "deceased"} <= set(reasons)
    assert all(s["coverage_end"] is None or s["coverage_end"] >= s["coverage_start"] for s in spans)


def test_pcp_attribution_and_cob_flags(data):
    npis = {r["npi"]: r for r in data.tables["provider"].to_pylist()}
    members = data.tables["member"].to_pylist()
    attributed = [m for m in members if m["pcp_npi"]]
    assert len(attributed) > 0.95 * len(members)
    for m in attributed:
        assert npis[m["pcp_npi"]]["specialty_key"] in (
            "family_medicine",
            "internal_medicine",
            "pediatrics",
        )
        assert npis[m["pcp_npi"]]["state"] == m["state"]
    assert Counter(m["pcp_attribution"] for m in members).keys() == {"assigned", "attributed"}
    cob = sum(1 for m in members if m["cob_flag"]) / len(members)
    assert 0.01 < cob < 0.10


def test_addresses_are_coherent(data):
    for m in data.tables["member"].to_pylist()[:500]:
        assert len(m["zip"]) == 5 and m["zip"].isdigit()
        assert -170 < m["longitude"] < -60 and 17 < m["latitude"] < 72
        assert m["county"] is None  # the bundled ZIP reference carries no county column


def test_provider_directory(data):
    rows = data.tables["provider"].to_pylist()
    assert {r["entity_type"] for r in rows} == {"1", "2"}
    for r in rows:
        spec = SPECIALTY[r["specialty_key"]]
        assert r["taxonomy_code"] is None  # the NUCC codes are bring-your-own (AMA copyright)
        assert r["specialty"] == spec.name
        assert r["network_status"] in ("IN", "OUT")
    assert Counter(r["network_status"] for r in rows)["OUT"] > 0
    assert any(r["provider_kind"] == "pharmacy" for r in rows)


def test_claim_lifecycle_statuses_and_reasons(data):
    claims = data.tables["medical_claim"].to_pylist()
    statuses = Counter(r["claim_status"] for r in claims)
    assert statuses.keys() >= {"paid", "denied", "adjusted", "reversed"}
    freq = Counter(r["claim_frequency_code"] for r in claims)
    assert freq.keys() == {"1", "7", "8"}
    for r in claims:
        if r["claim_status"] == "denied":
            assert r["denial_carc"] in CARC
            assert r["denial_rarc"] is None or r["denial_rarc"] in RARC
            assert r["total_paid"] == 0 and r["total_allowed"] == 0
        else:
            assert r["denial_carc"] is None
    denied = statuses["denied"] / sum(
        1 for r in claims if r["claim_version"] == 1 and not r["duplicate_of_claim_id"]
    )
    assert 0.03 < denied < 0.25


def test_reversals_negate_and_duplicates_are_denied(data):
    claims = {r["claim_id"]: r for r in data.tables["medical_claim"].to_pylist()}
    voids = [r for r in claims.values() if r["claim_frequency_code"] == "8"]
    dups = [r for r in claims.values() if r["duplicate_of_claim_id"]]
    assert voids and dups
    for v in voids:
        orig = claims[v["original_claim_id"]]
        assert v["claim_root_id"] == orig["claim_root_id"] and orig["claim_status"] == "reversed"
        assert (
            round(v["total_allowed"] + orig["total_allowed"], 2) == 0
            and round(v["total_paid"] + orig["total_paid"], 2) == 0
        )
    for d in dups:
        assert (
            d["denial_carc"] == "18"
            and d["denial_rarc"] == "N522"
            and claims[d["duplicate_of_claim_id"]]["claim_status"] != "denied"
        )


def test_resubmitted_and_adjusted_claims_chain_to_one_root(data):
    claims = data.tables["medical_claim"].to_pylist()
    by_root: dict[str, list[dict]] = {}
    for r in claims:
        by_root.setdefault(r["claim_root_id"], []).append(r)
    chains = [
        sorted(v, key=lambda r: r["claim_version"])
        for v in by_root.values()
        if len(v) > 1 and not v[0]["duplicate_of_claim_id"]
    ]
    assert len(chains) > 50
    corrected = adjusted = 0
    for chain in chains:
        for a, b in zip(chain, chain[1:], strict=False):
            assert (
                b["original_claim_id"] == a["claim_id"] and b["received_date"] >= a["received_date"]
            )
        if chain[0]["claim_status"] == "denied" and chain[1]["claim_frequency_code"] == "7":
            corrected += 1
        if chain[0]["claim_status"] == "adjusted":
            adjusted += 1
    assert corrected > 10 and adjusted > 0


def test_claim_dates_are_ordered(data):
    for r in data.tables["medical_claim"].to_pylist():
        assert (
            r["service_from_date"]
            <= r["service_to_date"]
            <= r["received_date"]
            <= r["adjudication_date"]
        )
        assert r["paid_date"] is None or r["paid_date"] >= r["adjudication_date"]


def test_prior_authorisation_precedes_service_and_missing_ones_deny(data):
    auths = {r["authorization_id"]: r for r in data.tables["prior_authorization"].to_pylist()}
    claims = data.tables["medical_claim"].to_pylist()
    with_auth = [r for r in claims if r["prior_auth_number"]]
    assert with_auth
    for r in with_auth:
        a = auths[r["prior_auth_number"]]
        if not a["retroactive"]:
            assert a["requested_date"] <= r["service_from_date"]
        assert a["status"].startswith("approved")
    assert any(r["denial_carc"] == "197" for r in claims)


def test_institutional_and_professional_claims_carry_the_right_fields(data):
    for r in data.tables["medical_claim"].to_pylist()[:5000]:
        if r["claim_type"] == "I":
            assert r["place_of_service"] is None and r["facility_type"]
            if r["facility_type"] == "inpatient":
                assert r["drg_code"] and r["admission_date"] and r["discharge_date"]
        else:
            assert r["place_of_service"] and r["facility_type"] is None and r["drg_code"] is None
    assert Counter(r["type_of_bill"] for r in data.tables["medical_claim"].to_pylist()) == {
        None: data.tables["medical_claim"].num_rows
    }


def test_inpatient_claims_carry_icd10pcs_for_procedures(data):
    procs = data.tables["claim_procedure"].to_pylist()
    stays = {
        r["claim_id"]
        for r in data.tables["medical_claim"].to_pylist()
        if r["facility_type"] == "inpatient"
    }
    assert procs and all(p["claim_id"] in stays and p["code_system"] == "ICD-10-PCS" for p in procs)


def test_modifiers_and_units(data):
    lines = data.tables["medical_claim_line"].to_pylist()
    assert all(r["units"] >= 1 for r in lines)
    assert {r["modifier_1"] for r in lines} <= {None, "GT"}
    assert any(r["modifier_1"] == "GT" and r["procedure_code"] for r in lines)
