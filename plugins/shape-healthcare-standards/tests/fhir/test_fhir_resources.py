"""FHIR R4 resource mapping: R4B model validation and round trips back to the source rows."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

import pytest
from fhir.resources.R4B import get_fhir_model_class
from shape_healthcare_standards.common import TableSet
from shape_healthcare_standards.contract import ContractError
from shape_healthcare_standards.fhir import resources as R
from shape_healthcare_standards.testing import sample_tables

ID_RE = re.compile(r"^[A-Za-z0-9\-.]{1,64}$")


@pytest.fixture(scope="module")
def ts() -> TableSet:
    return TableSet(sample_tables())


@pytest.fixture(scope="module")
def all_resources(ts: TableSet) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for table in R.TABLE_RESOURCES:
        out.extend(R.resources_for(table, ts))
    out.extend(R.payer_organizations(ts))
    return out


def of(resources: list[dict[str, Any]], rtype: str) -> dict[str, dict[str, Any]]:
    return {r["id"]: r for r in resources if r["resourceType"] == rtype}


def amount(adjs: list[dict[str, Any]], code: str) -> float | None:
    for a in adjs:
        if a["category"]["coding"][0]["code"] == code and "amount" in a:
            return float(a["amount"]["value"])
    return None


def test_every_sample_resource_validates_as_r4b(all_resources):
    kinds = {r["resourceType"] for r in all_resources}
    assert kinds == {
        "Patient",
        "Coverage",
        "Practitioner",
        "Organization",
        "Claim",
        "ExplanationOfBenefit",
        "MedicationDispense",
    }
    for r in all_resources:
        model = get_fhir_model_class(r["resourceType"]).model_validate(r)
        assert model.id == r["id"]


def test_ids_are_valid_unique_and_deterministic(ts, all_resources):
    keys = [(r["resourceType"], r["id"]) for r in all_resources]
    assert len(keys) == len(set(keys))
    assert all(ID_RE.match(i) for _, i in keys)
    again = [r for t in R.TABLE_RESOURCES for r in R.resources_for(t, ts)] + R.payer_organizations(
        ts
    )
    assert again == all_resources


def test_make_id_keeps_valid_keys_and_slugs_others():
    assert R.make_id("CLM-P-0001") == "CLM-P-0001"
    odd = R.make_id("a b/c_d é")
    assert ID_RE.match(odd) and odd != R.make_id("a b/c_d è")
    long_id = R.make_id("x" * 200)
    assert ID_RE.match(long_id) and len(long_id) <= 64
    assert R.make_id("k", "eob-") == "eob-k"


def test_icd10cm_dot():
    assert R.icd10cm_code("E119") == "E11.9"
    assert R.icd10cm_code("I10") == "I10"
    assert R.icd10cm_code("E1165") == "E11.65"


def test_patient_round_trip(ts, all_resources):
    patients = of(all_resources, "Patient")
    for row in ts.rows("member"):
        p = patients[R.patient_id(row["member_id"])]
        assert p["identifier"][0]["value"] == row["member_id"]
        assert p["name"][0]["family"] == row["last_name"]
        assert p["name"][0]["given"][0] == row["first_name"]
        assert p["birthDate"] == row["birth_date"].isoformat()
        assert p["gender"] == {"M": "male", "F": "female"}[row["sex"]]
        assert p["address"][0]["city"] == row["city"]
        assert p["address"][0]["postalCode"].replace("-", "") == row["zip"]
        phones = [t["value"].replace("-", "") for t in p.get("telecom", [])]
        assert phones == ([row["phone"]] if row["phone"] else [])
        assert "deceasedDateTime" not in p
    rex = {e["url"].rsplit("/", 1)[1]: e for e in patients["SYN100001-02"]["extension"]}
    assert rex["us-core-ethnicity"]["extension"][0]["valueCoding"]["code"] == "2135-2"
    assert rex["us-core-race"]["extension"][0]["valueCoding"]["code"] == "2106-3"
    assert patients["SYN100001-02"]["communication"][0]["language"]["coding"][0]["code"] == "es"
    assert patients["SYN200001-00"]["address"][0]["postalCode"] == "61604-0001"


def test_patient_deceased_and_unknown_sex(ts):
    row = dict(ts.rows("member")[0], deceased_date=dt.date(2024, 1, 2), sex="U", race=None)
    p = R.patient(row)
    assert p["deceasedDateTime"] == "2024-01-02" and p["gender"] == "unknown"
    assert all("us-core-race" not in e["url"] for e in p["extension"])
    get_fhir_model_class("Patient").model_validate(p)


def test_coverage_round_trip(ts, all_resources):
    cov = of(all_resources, "Coverage")
    for row in ts.rows("eligibility"):
        c = cov[row["eligibility_id"]]
        assert c["beneficiary"] == {"reference": f"Patient/{row['member_id']}"}
        assert c["period"]["start"] == row["coverage_start"].isoformat()
        assert c["period"].get("end") == (
            row["coverage_end"].isoformat() if row["coverage_end"] else None
        )
        assert c["status"] == ("cancelled" if row["coverage_end"] else "active")
        assert c["payor"] == [
            {"reference": "Organization/payer-SYNPAYER01", "display": row["payer_name"]}
        ]
        classes = {k["type"]["coding"][0]["code"]: k["value"] for k in c["class"]}
        assert classes == {"group": row["group_id"], "plan": row["plan_id"]}
    assert cov["E1"]["relationship"]["coding"][0]["code"] == "self"
    assert cov["E1"]["subscriberId"] == "SYN100001"
    assert cov["E2"]["relationship"]["coding"][0]["code"] == "spouse"
    assert cov["E2"]["subscriber"] == {"reference": "Patient/SYN100001-00"}
    assert cov["E3"]["relationship"]["coding"][0]["code"] == "child"
    assert cov["E3"]["dependent"] == "02"


def test_coverage_as_of_keeps_future_end_active(ts):
    ctx = R.Context(ts)
    row = dict(ts.rows("eligibility")[1])
    assert R.coverage(row, ctx)["status"] == "cancelled"
    assert R.coverage(row, ctx, as_of=dt.date(2024, 1, 1))["status"] == "active"


def test_provider_resources(ts, all_resources):
    prac, org = of(all_resources, "Practitioner"), of(all_resources, "Organization")
    for row in ts.rows("provider"):
        res = (prac if row["entity_type"] == "1" else org)[row["npi"]]
        ident = res["identifier"][0]
        assert ident == {"system": "http://hl7.org/fhir/sid/us-npi", "value": row["npi"]}
        if row["entity_type"] == "1":
            assert res["name"][0]["family"] == row["last_name"]
        else:
            assert res["name"] == row["org_name"]
    assert org["payer-SYNPAYER01"]["name"] == "Example Health Plan"
    with pytest.raises(ContractError):
        R.provider_resource(dict(ts.rows("provider")[0], entity_type="3"))


def test_claim_round_trip(ts, all_resources):
    claims = of(all_resources, "Claim")
    for row in ts.rows("medical_claim"):
        c = claims[R.claim_id(row["claim_id"])]
        lines = [x for x in ts.rows("medical_claim_line") if x["claim_id"] == row["claim_id"]]
        dx = [x for x in ts.rows("claim_diagnosis") if x["claim_id"] == row["claim_id"]]
        assert c["identifier"][0]["value"] == row["claim_id"]
        assert c["type"]["coding"][0]["code"] == (
            "institutional" if row["claim_type"] == "I" else "professional"
        )
        assert c["patient"] == {"reference": f"Patient/{row['member_id']}"}
        assert c["billablePeriod"] == {
            "start": row["service_from_date"].isoformat(),
            "end": row["service_to_date"].isoformat(),
        }
        assert c["total"] == {"value": row["total_billed"], "currency": "USD"}
        assert [i["sequence"] for i in c["item"]] == [x["line_number"] for x in lines]
        assert sum(i["net"]["value"] for i in c["item"]) == pytest.approx(row["total_billed"])
        assert [d["diagnosisCodeableConcept"]["coding"][0]["code"] for d in c["diagnosis"]] == [
            R.icd10cm_code(x["icd10_code"]) for x in dx
        ]
        for item, ln in zip(c["item"], lines, strict=True):
            assert item["servicedDate"] == ln["service_date"].isoformat()
            assert item["quantity"]["value"] == ln["units"]
            if ln["procedure_code"]:
                assert item["productOrService"]["coding"][0]["code"] == ln["procedure_code"]
            if ln["revenue_code"]:
                assert item["revenue"]["coding"][0] == {
                    "system": "https://www.nubc.org/CodeSystem/RevenueCodes",
                    "code": ln["revenue_code"],
                }
    p2 = claims["CLM-P-0002"]
    assert p2["insurance"][0]["preAuthRef"] == ["PA12345"]
    assert p2["insurance"][0]["coverage"] == {"reference": "Coverage/E2"}
    assert p2["item"][0]["modifier"][0]["coding"][0]["code"] == "25"
    assert p2["item"][0]["locationCodeableConcept"]["coding"][0]["code"] == "11"
    assert (
        p2["provider"]["reference"] == f"Organization/{ts.rows('medical_claim')[1]['billing_npi']}"
    )
    p3 = claims["CLM-P-0003"]
    assert p3["related"][0]["claim"]["identifier"]["value"] == "CLM-P-0003-ORIG"
    p1 = claims["CLM-P-0001"]
    assert p1["facility"]["identifier"]["value"] == ts.rows("medical_claim")[0]["facility_npi"]
    assert p1["item"][0]["diagnosisSequence"] == [1] and p1["item"][1]["diagnosisSequence"] == [
        1,
        2,
    ]


def test_institutional_claim_details(ts, all_resources):
    c = of(all_resources, "Claim")["CLM-I-0001"]
    assert c["diagnosis"][0]["packageCode"]["coding"][0]["code"] == "871"
    assert [d["onAdmission"]["coding"][0]["code"] for d in c["diagnosis"]] == ["Y"] * 4
    assert c["procedure"][0]["procedureCodeableConcept"]["coding"][0] == {
        "system": "http://www.cms.gov/Medicare/Coding/ICD10",
        "code": "0BH17EZ",
    }
    assert c["procedure"][0]["date"] == "2024-06-11"
    assert c["subType"]["coding"][0]["code"] == "111"
    info = {i["category"]["coding"][0]["code"]: i for i in c["supportingInfo"]}
    assert info["admissionperiod"]["timingPeriod"] == {"start": "2024-06-10", "end": "2024-06-14"}
    assert info["discharge"]["code"]["coding"][0]["code"] == "01"
    assert c["careTeam"][0]["provider"]["reference"].startswith("Practitioner/")
    assert c["item"][0]["revenue"]["coding"][0]["code"] == "0120"
    assert c["item"][2]["productOrService"]["coding"][0]["system"] == R.SYS_CPT
    assert c["item"][0]["productOrService"]["coding"][0]["code"] == "not-applicable"
    assert "locationCodeableConcept" not in c["item"][0]


def test_eob_round_trip(ts, all_resources):
    eobs = of(all_resources, "ExplanationOfBenefit")
    for row in ts.rows("medical_claim"):
        e = eobs[R.eob_id(row["claim_id"])]
        assert e["claim"] == {"reference": f"Claim/{R.claim_id(row['claim_id'])}"}
        assert e["payment"]["amount"]["value"] == row["total_paid"]
        assert e["payment"]["date"] == row["adjudication_date"].isoformat()
        totals = {t["category"]["coding"][0]["code"]: t["amount"]["value"] for t in e["total"]}
        assert totals == {
            "submitted": row["total_billed"],
            "eligible": row["total_allowed"],
            "benefit": row["total_paid"],
            "copay": row["member_copay"],
            "coinsurance": row["member_coinsurance"],
            "deductible": row["member_deductible"],
        }
        lines = [x for x in ts.rows("medical_claim_line") if x["claim_id"] == row["claim_id"]]
        for item, ln in zip(e["item"], lines, strict=True):
            adj = item["adjudication"]
            assert amount(adj, "submitted") == ln["billed_amount"]
            assert amount(adj, "eligible") == ln["allowed_amount"]
            assert amount(adj, "benefit") == ln["paid_amount"]
            assert amount(adj, "copay") == ln["copay"]
        assert e["insurer"]["reference"] == "Organization/payer-SYNPAYER01"
    assert eobs["eob-CLM-P-0001"]["outcome"] == "complete"
    assert eobs["eob-CLM-P-0003"]["outcome"] == "complete"
    p2 = eobs["eob-CLM-P-0002"]
    assert p2["outcome"] == "partial"
    reasons = [
        (a["reason"]["coding"][0]["system"], a["reason"]["coding"][0]["code"])
        for a in p2["item"][1]["adjudication"]
        if "reason" in a
    ]
    assert reasons == [(R.SYS_CARC, "50"), (R.SYS_RARC, "N115")]
    assert all("reason" not in a for a in p2["item"][0]["adjudication"])


def test_eob_outcomes_by_status(ts):
    row = dict(ts.rows("medical_claim")[0])
    with_lines = TableSet(
        {
            "medical_claim": ts.tables["medical_claim"],
            "medical_claim_line": ts.tables["medical_claim_line"],
        }
    )
    for status, outcome, eob_status in [
        ("denied", "error", "active"),
        ("pending", "queued", "active"),
        ("reversed", "complete", "cancelled"),
        ("paid", "complete", "active"),
    ]:
        e = R.explanation_of_benefit(dict(row, claim_status=status), R.Context(with_lines))
        assert (e["outcome"], e["status"]) == (outcome, eob_status), status
        get_fhir_model_class("ExplanationOfBenefit").model_validate(e)


def test_medication_dispense_round_trip(ts, all_resources):
    md = of(all_resources, "MedicationDispense")
    for row in ts.rows("pharmacy_claim"):
        d = md[R.make_id(row["rx_claim_id"])]
        coding = d["medicationCodeableConcept"]["coding"][0]
        assert coding == {"system": "http://hl7.org/fhir/sid/ndc", "code": row["ndc"]}
        assert d["quantity"]["value"] == row["quantity"]
        assert d["daysSupply"]["value"] == row["days_supply"]
        assert d["whenHandedOver"] == row["fill_date"].isoformat()
        assert d["subject"] == {"reference": f"Patient/{row['member_id']}"}
        assert d["performer"][0]["actor"]["reference"] == f"Organization/{row['pharmacy_npi']}"
    assert md["RX-0001"]["status"] == "completed"
    assert md["RX-0002"]["status"] == "cancelled"
    assert md["RX-0002"]["statusReasonCodeableConcept"]["coding"][0]["code"] == "79"
    assert md["RX-0003"]["status"] == "entered-in-error"
    assert md["RX-0001"]["medicationCodeableConcept"]["coding"][1]["code"] == "861007"
    assert md["RX-0001"]["medicationCodeableConcept"]["text"] == "Metformin 500 mg"


def test_primary_table_alone_uses_logical_references(ts):
    rows = TableSet({"medical_claim": ts.tables["medical_claim"]})
    ctx = R.Context(rows)
    c = R.claim(rows.rows("medical_claim")[0], ctx)
    assert c["provider"] == {
        "identifier": {"system": R.SYS_NPI, "value": ts.rows("provider")[0]["npi"]}
    }
    assert c["insurance"][0]["coverage"] == {
        "identifier": {"system": R.SYS_MEMBER, "value": "SYN100001-00"}
    }
    assert "item" not in c and "diagnosis" not in c
    get_fhir_model_class("Claim").model_validate(c)
    rx = TableSet({"pharmacy_claim": ts.tables["pharmacy_claim"]})
    d = R.medication_dispense(rx.rows("pharmacy_claim")[0], R.Context(rx))
    assert d["performer"][0]["actor"]["identifier"]["system"] == R.SYS_NPI
    get_fhir_model_class("MedicationDispense").model_validate(d)


def test_unmapped_table_is_a_contract_error(ts):
    with pytest.raises(ContractError):
        list(R.resources_for("claim_diagnosis", ts))
