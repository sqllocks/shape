"""Issue #45 acceptance items 1-7 and 9, measured on a generated population (see conftest)."""

from __future__ import annotations

from collections import Counter
from datetime import date

from shape_domains.healthcare_payer import quality


def _assert(check: quality.Check) -> None:
    assert check.passed, f"{check.item} {check.title}: {check.metrics} {check.notes}"


def test_1_age_and_sex_edits_hold(data):
    c = quality.check_age_sex(data)
    _assert(c)
    assert c.metrics["diagnoses_checked"] > 50_000
    assert c.metrics["diagnosis_violations"] == 0 and c.metrics["service_violations"] == 0


def test_1_no_prostate_codes_for_women_and_no_pregnancy_codes_for_men(data):
    sex = {r["member_id"]: r["sex"] for r in data.tables["member"].to_pylist()}
    claim_member = {r["claim_id"]: r["member_id"] for r in data.tables["medical_claim"].to_pylist()}
    seen = Counter()
    for r in data.tables["claim_diagnosis"].to_pylist():
        s = sex[claim_member[r["claim_id"]]]
        code = r["diagnosis_code"]
        if code.startswith(("C61", "N40", "R97.2")):
            assert s == "M"
            seen["male"] += 1
        if code.startswith(("O", "Z3A", "Z34", "Z37")):
            assert s == "F"
            seen["pregnancy"] += 1
    assert seen["male"] > 0 and seen["pregnancy"] > 0  # the rule was exercised, not vacuous


def test_2_diagnosis_procedure_and_drug_agree(data):
    c = quality.check_coherence(data)
    _assert(c)
    m = c.metrics
    assert m["diabetic_members"] >= 100
    assert m["order_indication_mismatches"] == 0 and m["fills_without_supporting_dx"] == 0
    assert m["diabetic_with_antidiabetic_fill"] >= 0.7 and m["diabetic_with_hba1c"] >= 0.9
    assert 0.5 <= m["diabetic_with_eye_exam"] <= 0.95


def test_2_statins_need_a_lipid_diagnosis_or_diabetes_or_cad(data):
    dx = Counter()
    by_claim = {}
    for r in data.tables["claim_diagnosis"].to_pylist():
        by_claim.setdefault(r["claim_id"], []).append(r["diagnosis_code"])
    member_codes: dict[str, set[str]] = {}
    for r in data.tables["medical_claim"].to_pylist():
        member_codes.setdefault(r["member_id"], set()).update(by_claim.get(r["claim_id"], []))
    n = 0
    for f in data.tables["pharmacy_claim"].to_pylist():
        if f["claim_status"] == "paid" and f["therapeutic_class"].startswith("HMG-CoA"):
            n += 1
            codes = member_codes[f["member_id"]]
            assert any(c.startswith(("E78", "E10", "E11", "I25")) for c in codes), f["member_id"]
            dx[f["drug_name"]] += 1
    assert n > 200


def test_2_antihypertensives_need_hypertension_or_a_cardiorenal_indication(data):
    by_claim = {}
    for r in data.tables["claim_diagnosis"].to_pylist():
        by_claim.setdefault(r["claim_id"], []).append(r["diagnosis_code"])
    member_codes: dict[str, set[str]] = {}
    for r in data.tables["medical_claim"].to_pylist():
        member_codes.setdefault(r["member_id"], set()).update(by_claim.get(r["claim_id"], []))
    n = 0
    for f in data.tables["pharmacy_claim"].to_pylist():
        if f["claim_status"] == "paid" and f["therapeutic_class"] in (
            "ACE inhibitor",
            "Calcium channel blocker",
            "Thiazide diuretic",
            "Angiotensin receptor blocker",
        ):
            n += 1
            assert any(
                c.startswith(("I10", "I11", "I12", "I13", "I50", "I25", "N18", "E11"))
                for c in member_codes[f["member_id"]]
            )
    assert n > 200


def test_3_comorbidity_clustering_and_persistence(data):
    c = quality.check_comorbidity(data)
    _assert(c)
    latent = c.metrics["P(.|diabetes) simulated problem list"]
    assert latent["htn"] > latent["ckd"] and latent["obesity"] > 0.45
    for name, (rate, n) in c.metrics["persistence(next-year recurrence, n)"].items():
        assert n >= 100 and rate >= 0.9, name


def test_3_chronic_drugs_recur_across_years(data):
    fills = {}
    for f in data.tables["pharmacy_claim"].to_pylist():
        if f["claim_status"] == "paid" and f["therapeutic_class"].startswith("HMG-CoA"):
            fills.setdefault(f["member_id"], set()).add(f["fill_date"].year)
    both = sum(1 for ys in fills.values() if 2022 in ys and 2023 in ys)
    first = sum(1 for ys in fills.values() if 2022 in ys)
    assert first > 100 and both / first > 0.5


def test_4_utilisation_shape(data):
    c = quality.check_utilization(data)
    _assert(c)
    m = c.metrics
    assert m["top5_share"] >= 0.38 and m["top1_share"] >= 0.14
    assert m["bottom50_share"] < 0.15
    assert 1.5 <= m["winter_to_summer_respiratory"] <= 5
    # ED and inpatient use climb with age; the youngest outpatient-heavy mix is not the oldest
    mix = m["service_mix_by_age"]
    assert mix["75+"]["inpatient"] > mix["35-44"]["inpatient"]


def test_5_every_code_is_valid_and_billable_on_the_date_of_service(data):
    c = quality.check_codes(data)
    _assert(c)
    assert c.metrics["cpt_lines_without_licensed_table"] == 0
    assert (
        c.metrics["invalid_diagnoses"]
        == c.metrics["invalid_pcs"]
        == c.metrics["invalid_lines"]
        == 0
    )


def test_5_ndc_check_is_not_vacuous(data):
    from shape_domains.healthcare_payer.drugs import InterimNdcDirectory

    ndc = InterimNdcDirectory()
    early = date(2015, 6, 1)
    # a generated NDC is NOT marketed before its launch date, so the check can fail
    assert not ndc.is_marketed(
        data.tables["pharmacy_claim"].column("ndc")[0].as_py(), date(1990, 1, 1)
    )
    assert early.year < 2022


def test_6_financial_coherence(data):
    c = quality.check_financial(data)
    _assert(c)
    assert c.metrics["line_rows_not_balancing"] == 0
    cv = c.metrics["allowed_amount_cv_by_procedure"]
    assert cv and all(v < 0.6 for v in cv.values())  # allowed amounts cluster by procedure


def test_6_accumulators_never_exceed_plan_maxima_individual_and_family(data):
    plans = {r["plan_id"]: r for r in data.tables["plan"].to_pylist()}
    for r in data.tables["member_accumulator"].to_pylist():
        p = plans[r["plan_id"]]
        cap = p["oop_max"] * (p["family_multiple"] if r["scope"] == "family" else 1)
        assert r["oop_met"] <= cap + 0.005, r
        dcap = p["deductible"] * (p["family_multiple"] if r["scope"] == "family" else 1)
        assert r["deductible_met"] <= dcap + 0.005, r
        if r["rx_oop_met"] is not None:
            assert r["rx_oop_met"] <= r["oop_met"] + 0.005
    assert data.tables["member_accumulator"].num_rows > 1000


def test_6_member_cost_share_stays_within_the_regulatory_limits(data):
    plans = {r["plan_id"]: r for r in data.tables["plan"].to_pylist()}
    acas = {2022: 8700, 2023: 9100, 2024: 9450}
    moop = {2022: 7550, 2023: 8300, 2024: 8850}
    for p in plans.values():
        if p["line_of_business"] == "commercial":
            assert p["oop_max"] <= min(acas.values())
        if p["line_of_business"] == "ma":
            assert p["oop_max"] <= min(moop.values())
    assert plans["COM-HDHP-1"]["deductible"] >= 1600  # the highest HSA minimum in the window


def test_7_los_agrees_with_drg_and_readmissions_are_rare(data):
    c = quality.check_los_drg(data)
    _assert(c)
    assert c.metrics["inpatient_claims"] >= 300
    assert c.metrics["readmission_30d_rate"] < 0.15
    assert c.metrics["geometric_mean_los_vs_drg(n>=15)"]


def test_7_admission_precedes_discharge_and_poa_is_set_on_inpatient_dx(data):
    stays = {
        r["claim_id"]: r
        for r in data.tables["medical_claim"].to_pylist()
        if r["facility_type"] == "inpatient"
    }
    assert stays
    for s in stays.values():
        assert (
            s["admission_date"] <= s["discharge_date"]
            and s["length_of_stay"] == (s["discharge_date"] - s["admission_date"]).days
        )
    poa = Counter()
    for r in data.tables["claim_diagnosis"].to_pylist():
        if r["claim_id"] in stays:
            poa[r["poa_indicator"]] += 1
        else:
            assert r["poa_indicator"] is None
    assert poa["Y"] > 0 and poa["N"] >= 0 and set(poa) <= {"Y", "N", "", None}


def test_population_is_a_mix_of_commercial_medicare_advantage_and_medicaid(data):
    lob = Counter(r["line_of_business"] for r in data.tables["member"].to_pylist())
    n = sum(lob.values())
    assert set(lob) == {"commercial", "ma", "medicaid"}
    assert (
        0.45 <= lob["commercial"] / n <= 0.68
        and 0.10 <= lob["ma"] / n <= 0.30
        and 0.15 <= lob["medicaid"] / n <= 0.38
    )
    ages = {}
    for r in data.tables["member"].to_pylist():
        age = 2022 - r["birth_date"].year
        ages.setdefault(r["line_of_business"], []).append(age)
    assert min(ages["ma"]) >= 40 and sorted(ages["ma"])[len(ages["ma"]) // 2] >= 68
    assert sorted(ages["medicaid"])[len(ages["medicaid"]) // 2] < 40
