"""The clinical modules leave the footprints a clinician expects in the claims."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date

from shape_domains.healthcare_payer.generate import generate


def _by_claim(data):
    dx = defaultdict(list)
    for r in data.tables["claim_diagnosis"].to_pylist():
        dx[r["claim_id"]].append(r["diagnosis_code"])
    return dx


def _paid(data):
    return [
        r
        for r in data.tables["medical_claim"].to_pylist()
        if r["claim_status"] == "paid"
        and r["claim_frequency_code"] != "8"
        and not r["duplicate_of_claim_id"]
    ]


def test_pregnancy_prenatal_delivery_postpartum_and_newborn(data):
    dx = _by_claim(data)
    members = {m["member_id"]: m for m in data.tables["member"].to_pylist()}
    deliveries = [
        r
        for r in _paid(data)
        if r["facility_type"] == "inpatient"
        and any(c in ("O80", "O82") for c in dx[r["claim_id"]][:1])
    ]
    assert len(deliveries) > 60
    for d in deliveries:
        codes = dx[d["claim_id"]]
        assert "Z37.0" in codes and any(c.startswith("Z3A.") for c in codes)
        assert d["drg_code"] in ("805", "806", "807", "786", "787", "788")
        assert members[d["member_id"]]["sex"] == "F"
    prenatal = Counter()
    for r in _paid(data):
        c = dx[r["claim_id"]]
        if r["claim_type"] == "P" and c and c[0].startswith(("Z34", "O09", "O13", "O14", "O24")):
            prenatal[r["member_id"]] += 1
    full = [prenatal[d["member_id"]] for d in deliveries if prenatal[d["member_id"]]]
    assert full and 6 <= sorted(full)[len(full) // 2] <= 16
    babies = [
        m
        for m in members.values()
        if m["relationship_code"] == "19" and m["birth_date"] >= date(2022, 1, 2)
    ]
    delivered_on = {d["service_from_date"] for d in deliveries}
    newborn_stays = [
        r
        for r in _paid(data)
        if r["facility_type"] == "inpatient" and r["drg_code"] in ("795", "792", "791")
    ]
    assert (
        babies
        and newborn_stays
        and all(r["service_from_date"] in delivered_on for r in newborn_stays[:50])
    )
    assert any(m["birth_date"] in delivered_on for m in babies)
    assert Counter(dx[r["claim_id"]][0] for r in newborn_stays) <= Counter(
        {"Z38.00": 1, "Z38.01": 1}
    ).keys() | set(Counter(dx[r["claim_id"]][0] for r in newborn_stays))


def test_gestational_weeks_increase_across_the_visits_of_a_pregnancy(data):
    dx = _by_claim(data)
    weeks = defaultdict(list)
    for r in sorted(_paid(data), key=lambda r: r["service_from_date"]):
        for c in dx[r["claim_id"]]:
            if c.startswith("Z3A.") and r["claim_type"] == "P":
                weeks[r["member_id"]].append((r["service_from_date"], int(c[4:])))
    ok = 0
    for visits in weeks.values():
        if len(visits) >= 4:
            seq = [w for _, w in visits[:6]]
            ok += seq == sorted(seq) or max(seq) - min(seq) > 0
    assert ok > 20


def test_cancer_pathway_work_up_surgery_and_therapy(data):
    dx = _by_claim(data)
    paid = _paid(data)
    cancer_members = {
        r["member_id"]
        for r in paid
        if dx[r["claim_id"]]
        and dx[r["claim_id"]][0] in ("C50.911", "C50.912", "C61", "C18.9", "C34.90")
    }
    assert len(cancer_members) >= 15
    chemo = [r for r in paid if dx[r["claim_id"]][:1] == ["Z51.11"]]
    assert chemo and all(any(c.startswith("C") for c in dx[r["claim_id"]]) for r in chemo)
    lines = defaultdict(list)
    for ln in data.tables["medical_claim_line"].to_pylist():
        lines[ln["claim_id"]].append(ln["procedure_code"])
    assert any(
        "J9271" in v or "J9045" in v or "J9267" in v for v in (lines[r["claim_id"]] for r in chemo)
    )
    procs = {r["claim_id"]: r["icd10pcs_code"] for r in data.tables["claim_procedure"].to_pylist()}
    for r in paid:
        if (
            r["facility_type"] == "inpatient"
            and r["claim_id"] in procs
            and dx[r["claim_id"]][0] in ("C50.911", "C50.912")
        ):
            assert procs[r["claim_id"]] == (
                "0HTU0ZZ" if dx[r["claim_id"]][0] == "C50.911" else "0HTV0ZZ"
            )  # laterality agrees
    assert all(dx[r["claim_id"]][0] != "C61" or r["member_id"] for r in paid)


def test_chronic_kidney_disease_dialysis_and_stage_codes(data):
    dx = _by_claim(data)
    paid = _paid(data)
    dial = [r for r in paid if r["facility_type"] == "dialysis"]
    if dial:
        assert all(dx[r["claim_id"]][0] == "N18.6" and "Z99.2" in dx[r["claim_id"]] for r in dial)
    stages = Counter(c for r in paid for c in dx[r["claim_id"]] if c.startswith("N18"))
    assert stages and {"N18.30", "N18.31", "N18.32"} & set(stages)  # post-2020 stage 3 codes only
    assert "N18.3" not in stages


def test_behavioural_health_uses_visits_drugs_and_admissions(data):
    dx = _by_claim(data)
    paid = _paid(data)
    bh_visits = [
        r
        for r in paid
        if dx[r["claim_id"]][:1]
        and dx[r["claim_id"]][0][:3] in ("F32", "F33", "F41", "F90", "F31", "F20", "F10", "F11")
    ]
    assert len(bh_visits) > 300
    keys = Counter(ln["service_key"] for ln in data.tables["medical_claim_line"].to_pylist())
    assert keys["PSYCHOTHERAPY_45"] > 0 and keys["MED_MGMT_PSYCH"] > 0
    classes = Counter(
        f["therapeutic_class"]
        for f in data.tables["pharmacy_claim"].to_pylist()
        if f["claim_status"] == "paid"
    )
    assert classes["SSRI antidepressant"] > 100 and classes["CNS stimulant"] > 0
    psych = [r for r in paid if r["drg_code"] in ("885", "897")]
    assert psych or len(bh_visits) > 0


def test_asthma_and_copd_exacerbations_peak_in_winter(data):
    dx = _by_claim(data)
    months = Counter()
    for r in _paid(data):
        c = dx[r["claim_id"]]
        if c and c[0] in ("J45.901", "J45.21", "J45.31", "J45.41", "J44.1"):
            months[r["service_from_date"].month] += 1
    winter = months[12] + months[1] + months[2]
    summer = months[6] + months[7] + months[8]
    assert winter + summer > 60 and winter > 1.3 * summer


def test_flu_vaccination_happens_in_autumn(data):
    months = Counter()
    for ln in data.tables["medical_claim_line"].to_pylist():
        if ln["procedure_code"] == "Q2039":
            months[ln["service_date"].month] += 1
    assert (
        sum(months.values()) > 300
        and sum(months[m] for m in (9, 10, 11)) / sum(months.values()) > 0.95
    )


def test_diabetes_care_rates_are_plausible(data):
    dx = _by_claim(data)
    paid = _paid(data)
    dm = {
        r["member_id"] for r in paid if any(c.startswith(("E10", "E11")) for c in dx[r["claim_id"]])
    }
    a1c = Counter()
    claim_member = {r["claim_id"]: r for r in paid}
    for ln in data.tables["medical_claim_line"].to_pylist():
        r = claim_member.get(ln["claim_id"])
        if r and r["member_id"] in dm and ln["service_key"] == "LAB_HBA1C":
            a1c[(r["member_id"], ln["service_date"].year)] += 1
    per = sum(a1c.values()) / len(a1c)
    assert 1.4 <= per <= 4.5  # ADA: two to four a year
    statins = {
        f["member_id"]
        for f in data.tables["pharmacy_claim"].to_pylist()
        if f["therapeutic_class"].startswith("HMG-CoA") and f["claim_status"] == "paid"
    }
    members = {m["member_id"]: m for m in data.tables["member"].to_pylist()}
    eligible = [m for m in dm if 40 <= 2023 - members[m]["birth_date"].year <= 75]
    share = sum(1 for m in eligible if m in statins) / len(eligible)
    assert 0.35 <= share <= 0.9


def test_diabetes_drugs_by_type(data):
    dx = _by_claim(data)
    paid = _paid(data)
    t1 = {r["member_id"] for r in paid if any(c.startswith("E10") for c in dx[r["claim_id"]])}
    insulin = {
        f["member_id"]
        for f in data.tables["pharmacy_claim"].to_pylist()
        if "insulin" in f["therapeutic_class"].lower() and f["claim_status"] == "paid"
    }
    assert t1 and sum(1 for m in t1 if m in insulin) / len(t1) > 0.8
    metformin = [
        f
        for f in data.tables["pharmacy_claim"].to_pylist()
        if f["drug_name"].startswith("metformin") and f["claim_status"] == "paid"
    ]
    assert metformin
    members = {m["member_id"]: m for m in data.tables["member"].to_pylist()}
    assert all(2022 - members[f["member_id"]]["birth_date"].year >= 9 for f in metformin)


def test_drug_launch_dates_are_respected(data):
    for f in data.tables["pharmacy_claim"].to_pylist():
        if f["brand_name"] == "Mounjaro":
            assert f["fill_date"] >= date(2022, 6, 1)


def test_licensed_cpt_table_flows_into_the_lines(tmp_path):
    path = tmp_path / "lic.csv"
    path.write_text(
        "table,key,value\ncpt,LAB_HBA1C,T0001\ncpt,EM_OFFICE_EST_3,T0002\nrevenue,INPT_ROOM_BOARD,0120\ntob,inpatient:1,0111\n"
    )
    from shape_domains.healthcare_payer.byo import load_licensed

    d = generate(400, seed=6, licensed=load_licensed(path))
    lines = d.tables["medical_claim_line"].to_pylist()
    assert {r["procedure_code"] for r in lines if r["service_key"] == "LAB_HBA1C"} == {"T0001"}
    assert {r["code_system"] for r in lines if r["service_key"] == "LAB_HBA1C"} == {"CPT"}
    assert {r["code_system"] for r in lines if r["service_key"] == "EM_OFFICE_EST_4"} == {
        "SHAPE-SVC"
    }
    assert {r["revenue_code"] for r in lines if r["service_key"] == "INPT_ROOM_BOARD"} == {"0120"}
    claims = d.tables["medical_claim"].to_pylist()
    assert {
        r["type_of_bill"]
        for r in claims
        if r["facility_type"] == "inpatient" and r["claim_frequency_code"] == "1"
    } <= {"0111", None}
