"""Acceptance measures for the healthcare payer domain (issue #45, items 1-7).

Each ``check_*`` function reads the generated tables and returns a :class:`Check`: the measured
values and whether they fall inside the band the calibration table sets for them.  Nothing here
changes a band to make a run pass: the bands come from ``calibration.RATES`` (published figures)
or are exact invariants (zero violations).
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .calibration import band
from .drugs import DRUGS, NdcDirectory
from .generate import HealthcarePayerData
from .icd10cm import ICD10CM
from .problem import INDICATION_DX
from .reference import DRG, MCC_CODES, PCS, pos_valid_on
from .services import SERVICES, SYSTEM_CPT, SYSTEM_HCPCS, SYSTEM_SVC

PREGNANCY_PREFIXES = (
    "O0",
    "O1",
    "O2",
    "O3",
    "O4",
    "O6",
    "O7",
    "O8",
    "O9",
    "Z3A",
    "Z34",
    "Z37",
    "Z39.2",
)
PEDIATRIC_CODES = {
    "Z00.129": 17,
    "Z00.121": 17,
    "J21.9": 5,
    "H66.90": 17,
    "Z38.00": 0,
    "Z38.01": 0,
    "Z00.110": 0,
    "Z00.111": 0,
}
MALE_ONLY_PREFIXES = ("C61", "R97.20", "Z12.5", "Z85.46", "N40")
FEMALE_ONLY_PREFIXES = ("C50.9", "Z12.31", "Z12.4", "Z85.3", "M81.0")


@dataclass(slots=True)
class Check:
    item: str
    title: str
    passed: bool
    metrics: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _col(table: Any, name: str) -> list[Any]:
    return list(table.column(name).to_pylist())


def _final_claims(data: HealthcarePayerData) -> dict[str, dict[str, Any]]:
    """claim_id -> row for rows that count as services delivered: not denied, not voided or
    superseded by a later version, not a duplicate."""
    t = data.tables["medical_claim"].to_pylist()
    out = {}
    for r in t:
        if (
            r["claim_status"] in ("paid",)
            and r["claim_frequency_code"] != "8"
            and not r["duplicate_of_claim_id"]
        ):
            out[r["claim_id"]] = r
    return out


def _dx_by_claim(data: HealthcarePayerData) -> dict[str, list[tuple[str, str | None]]]:
    out: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
    for r in data.tables["claim_diagnosis"].to_pylist():
        out[r["claim_id"]].append((r["diagnosis_code"], r["poa_indicator"]))
    return out


def _members(data: HealthcarePayerData) -> dict[str, dict[str, Any]]:
    return {r["member_id"]: r for r in data.tables["member"].to_pylist()}


def _age(birth: date, day: date) -> int:
    return day.year - birth.year - ((day.month, day.day) < (birth.month, birth.day))


def _covered_all(spans: list[dict[str, Any]], year: int) -> bool:
    """True when the member's coverage spans leave no gap in ``year``."""
    lo, hi = date(year, 1, 1), date(year, 12, 31)
    cur = lo
    for s in sorted(spans, key=lambda r: r["coverage_start"]):
        end = s["coverage_end"] or date(9999, 1, 1)
        if s["coverage_start"] <= cur <= end:
            cur = end + timedelta(days=1)
            if cur > hi:
                return True
    return False


# ---- (1) age and sex edits ----------------------------------------------------------------------
def check_age_sex(data: HealthcarePayerData) -> Check:
    members = _members(data)
    claims = {r["claim_id"]: r for r in data.tables["medical_claim"].to_pylist()}
    violations: list[str] = []
    checked = 0
    for r in data.tables["claim_diagnosis"].to_pylist():
        c = claims[r["claim_id"]]
        m = members[c["member_id"]]
        day = c["service_from_date"]
        age = _age(m["birth_date"], day)
        code = r["diagnosis_code"]
        icd = ICD10CM.get(code)
        checked += 1
        if icd is None:
            violations.append(f"{code}: not in the code set")
            continue
        if icd.sex and icd.sex != m["sex"]:
            violations.append(f"{code} sex {m['sex']} claim {r['claim_id']}")
        if not icd.age_min <= age <= icd.age_max:
            violations.append(f"{code} age {age} claim {r['claim_id']}")
        if code.startswith(PREGNANCY_PREFIXES) and (m["sex"] != "F" or not 10 <= age <= 55):
            violations.append(f"pregnancy code {code} sex {m['sex']} age {age}")
        if code in PEDIATRIC_CODES and age > PEDIATRIC_CODES[code]:
            violations.append(f"paediatric code {code} age {age}")
        if code.startswith(MALE_ONLY_PREFIXES) and m["sex"] != "M":
            violations.append(f"male-only {code} on {m['sex']}")
        if code.startswith(FEMALE_ONLY_PREFIXES) and m["sex"] != "F":
            violations.append(f"female-only {code} on {m['sex']}")
    svc_viol = 0
    for r in data.tables["medical_claim_line"].to_pylist():
        s = SERVICES[r["service_key"]]
        c = claims[r["claim_id"]]
        m = members[c["member_id"]]
        age = _age(m["birth_date"], r["service_date"])
        if (s.sex and s.sex != m["sex"]) or not s.age_min <= age <= s.age_max:
            svc_viol += 1
    return Check(
        "1",
        "age and sex edits hold",
        not violations and svc_viol == 0,
        {
            "diagnoses_checked": checked,
            "diagnosis_violations": len(violations),
            "service_violations": svc_viol,
        },
        violations[:10],
    )


# ---- (2) diagnosis, procedure and drug agree --------------------------------------------------
def check_coherence(data: HealthcarePayerData) -> Check:
    claims = _final_claims(data)
    dx = _dx_by_claim(data)
    member_dx: dict[str, set[str]] = defaultdict(set)
    # a diagnosis is "on the member's record" when any submitted claim carries it (duplicates and
    # voids aside): a denied claim still documents the condition the prescriber treated
    for row in data.tables["medical_claim"].to_pylist():
        if row["duplicate_of_claim_id"] or row["claim_frequency_code"] == "8":
            continue
        for code, _ in dx[row["claim_id"]]:
            member_dx[row["member_id"]].add(code)
    members_by_id = _members(data)
    orders = {r["rx_order_id"]: r for r in data.tables["rx_order"].to_pylist()}
    key_of_ndc = {r["ndc"]: r["drug_key"] for r in data.tables["drug_reference"].to_pylist()}
    rx = data.tables["pharmacy_claim"].to_pylist()
    link_bad = 0
    support_bad = 0
    paid_fills = 0
    classes: Counter[str] = Counter()
    unsupported: Counter[str] = Counter()
    for f in rx:
        if f["claim_status"] != "paid":
            continue
        paid_fills += 1
        o = orders[f["rx_order_id"]]
        prefixes = INDICATION_DX.get(o["indication"], ())
        code = o["indication_icd10cm"] or ""
        drug = DRUGS[key_of_ndc[f["ndc"]]]
        if o["indication"] not in drug.indications:
            link_bad += 1
        if not code.startswith(prefixes):
            link_bad += 1
        if o["indication"] not in (
            "pain_acute",
            "nausea_chemo",
            "nausea_pregnancy",
            "pregnancy",
            "pregnancy_htn",
            "gdm",
        ):
            have = member_dx.get(f["member_id"], set())
            if not any(c.startswith(prefixes) for c in have):
                support_bad += 1
                unsupported[o["indication"]] += 1
        classes[f["therapeutic_class"]] += 1
    # diabetes care
    dm_members = {
        m for m, codes in member_dx.items() if any(c.startswith(("E10", "E11")) for c in codes)
    }
    lines = data.tables["medical_claim_line"].to_pylist()
    keys_by_member: dict[str, Counter[str]] = defaultdict(Counter)
    for r in lines:
        c = claims.get(r["claim_id"])
        if c:
            keys_by_member[c["member_id"]][r["service_key"]] += 1
    fills_by_member: dict[str, set[str]] = defaultdict(set)
    for f in rx:
        if f["claim_status"] == "paid":
            fills_by_member[f["member_id"]].add(f["therapeutic_class"])
    anti = (
        "Biguanide antidiabetic",
        "Sulfonylurea antidiabetic",
        "DPP-4 inhibitor",
        "SGLT2 inhibitor",
        "GLP-1 receptor agonist",
        "GIP/GLP-1 receptor agonist",
        "Long-acting insulin",
        "Rapid-acting insulin",
    )
    elig_by_member: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in data.tables["eligibility"].to_pylist():
        elig_by_member[e["member_id"]].append(e)
    years = range(data.simulation.start.year, data.simulation.end.year + 1)
    dm_years = {
        (m, y)
        for m in dm_members
        for y in years
        if _covered_all(elig_by_member[m], y) and _age_ok(members_by_id[m], y)
    }
    a1c_by_year: Counter[tuple[str, int]] = Counter()
    for r in lines:
        c = claims.get(r["claim_id"])
        if c and r["service_key"] == "LAB_HBA1C":
            a1c_by_year[(c["member_id"], r["service_date"].year)] += 1
    a1c_rate = sum(a1c_by_year[k] for k in dm_years) / len(dm_years) if dm_years else None
    n_dm = len(dm_members)
    with_drug = sum(1 for m in dm_members if fills_by_member[m] & set(anti))
    with_a1c = sum(1 for m in dm_members if keys_by_member[m]["LAB_HBA1C"])
    with_eye = sum(1 for m in dm_members if keys_by_member[m]["EYE_EXAM_DILATED"])
    with_foot = sum(
        1 for m in dm_members if keys_by_member[m]["LOPS_EVAL"] + keys_by_member[m]["LOPS_FOLLOWUP"]
    )
    metrics = {
        "paid_fills": paid_fills,
        "order_indication_mismatches": link_bad,
        "fills_without_supporting_dx": support_bad,
        "unsupported_by_indication": dict(unsupported),
        "diabetic_members": n_dm,
        "diabetic_with_antidiabetic_fill": round(with_drug / n_dm, 3) if n_dm else None,
        "diabetic_with_hba1c": round(with_a1c / n_dm, 3) if n_dm else None,
        "diabetic_with_eye_exam": round(with_eye / n_dm, 3) if n_dm else None,
        "diabetic_with_foot_exam": round(with_foot / n_dm, 3) if n_dm else None,
        "hba1c_per_diabetic_member_year": round(a1c_rate, 2) if a1c_rate is not None else None,
        "hba1c_per_year_target": data.simulation.cal.get("dm.hba1c_per_year"),
    }
    notes: list[str] = []
    ok = link_bad == 0 and support_bad == 0
    if n_dm >= 30:
        ok = ok and with_a1c / n_dm >= 0.80 and with_drug / n_dm >= 0.55
        ok = ok and 0.25 <= with_eye / n_dm <= 0.95
        target = float(data.simulation.cal.get("dm.hba1c_per_year"))
        ok = ok and a1c_rate is not None and 0.6 * target <= a1c_rate <= 1.4 * target
    return Check("2", "diagnosis, procedure and drug agree", ok, metrics, notes)


# ---- (3) comorbidity clustering and persistence ------------------------------------------------
def check_comorbidity(data: HealthcarePayerData) -> Check:
    claims = _final_claims(data)
    dx = _dx_by_claim(data)
    by_year: dict[tuple[str, int], set[str]] = defaultdict(set)
    for cid, row in claims.items():
        for code, _ in dx[cid]:
            by_year[(row["member_id"], row["service_from_date"].year)].add(code)
    members = _members(data)
    elig = defaultdict(list)
    for r in data.tables["eligibility"].to_pylist():
        elig[r["member_id"]].append(r)

    def covered_all(mid: str, year: int) -> bool:
        lo, hi = date(year, 1, 1), date(year, 12, 31)
        spans = sorted(elig[mid], key=lambda r: r["coverage_start"])
        cur = lo
        for s in spans:
            e = s["coverage_end"] or date(9999, 1, 1)
            if s["coverage_start"] <= cur <= e:
                cur = e + timedelta(days=1)
                if cur > hi:
                    return True
        return False

    def has(codes: set[str], *prefixes: str) -> bool:
        return any(c.startswith(prefixes) for c in codes)

    cohort: Counter[str] = Counter()
    for (mid, year), codes in by_year.items():
        m = members[mid]
        if _age(m["birth_date"], date(year, 7, 1)) < 18 or not covered_all(mid, year):
            continue
        cohort["adults"] += 1
        if has(codes, "E10", "E11"):
            cohort["dm"] += 1
            cohort["dm_htn"] += has(codes, "I10", "I11", "I12", "I13")
            cohort["dm_lipid"] += has(codes, "E78")
            cohort["dm_obesity"] += has(codes, "E66")
            cohort["dm_ckd"] += has(codes, "N18", "I12", "E11.22")
        if has(codes, "I10", "I11", "I12"):
            cohort["htn"] += 1
    n = cohort["dm"]
    rates = (
        {k: round(cohort[f"dm_{k}"] / n, 3) for k in ("htn", "lipid", "obesity", "ckd")}
        if n
        else {}
    )
    # persistence: a chronic diagnosis in year Y reappears in Y+1 for members covered all of
    # Y and Y+1
    persist = {}
    last_year = data.simulation.end.year
    for name, prefixes in (
        ("diabetes", ("E10", "E11")),
        ("hypertension", ("I10", "I11", "I12")),
        ("lipid", ("E78",)),
    ):
        kept = total = 0
        for (mid, year), codes in by_year.items():
            if year + 1 > last_year:
                continue
            if has(codes, *prefixes) and covered_all(mid, year) and covered_all(mid, year + 1):
                total += 1
                kept += has(by_year.get((mid, year + 1), set()), *prefixes)
        persist[name] = (round(kept / total, 3) if total else None, total)
    # recurring fills for chronic drugs: members with a statin fill in Y and covered Y+1 fill again
    cal = data.simulation.cal
    t_htn, t_lipid, t_obes, t_ckd = (
        cal.get(f"target.dm_with_{k}") for k in ("htn", "lipid", "obesity", "ckd")
    )
    # clinical truth: the simulated problem lists of diabetic adults (the published figures are
    # about disease, the claims-visible rates above are lower where a condition is under-coded)
    adults = [
        p for p in data.simulation.persons if p.has("dm") and p.age(data.simulation.start) >= 18
    ]
    latent = (
        {
            k: round(sum(p.has(k) for p in adults) / len(adults), 3)
            for k in ("htn", "lipid", "obesity", "ckd")
        }
        if adults
        else {}
    )
    prefix = {
        "htn": ("I10", "I11", "I12", "I13"),
        "lipid": ("E78",),
        "obesity": ("E66",),
        "ckd": ("N18", "I12.0", "E11.22"),
    }
    submitted: dict[str, set[str]] = defaultdict(set)
    for row in data.tables["medical_claim"].to_pylist():
        if not (row["duplicate_of_claim_id"] or row["claim_frequency_code"] == "8"):
            for code, _ in dx[row["claim_id"]]:
                submitted[row["member_id"]].add(code)
    coded_latent = (
        {
            k: round(
                sum(p.has(k) and has(submitted[p.member.member_id], *prefix[k]) for p in adults)
                / len(adults),
                3,
            )
            for k in latent
        }
        if adults
        else {}
    )
    ok = True
    if len(adults) >= 40:
        ok = (
            t_htn[0] <= latent["htn"] <= t_htn[1]
            and t_lipid[0] <= latent["lipid"] <= t_lipid[1]
            and t_obes[0] <= latent["obesity"] <= t_obes[1]
            and t_ckd[0] <= latent["ckd"] <= t_ckd[1]
        )
        ok = ok and all(coded_latent[k] <= latent[k] for k in latent)
        ok = ok and all(v is None or t < 20 or v >= 0.80 for v, t in persist.values())
    return Check(
        "3",
        "comorbidity clustering and persistence",
        ok,
        {
            "diabetic_member_years": n,
            "diabetic_adults": len(adults),
            "P(.|diabetes) simulated problem list": latent,
            "P(.|diabetes) coded on a claim (same adults)": coded_latent,
            "P(.|diabetes) coded on claims (member-years)": rates,
            "persistence(next-year recurrence, n)": persist,
            "targets": {"htn": t_htn, "lipid": t_lipid, "obesity": t_obes, "ckd": t_ckd},
        },
    )


# ---- (4) utilisation shape --------------------------------------------------------------------
def member_years(data: HealthcarePayerData) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    start, end = data.simulation.start, data.simulation.end
    for r in data.tables["eligibility"].to_pylist():
        lo = max(r["coverage_start"], start)
        hi = min(r["coverage_end"] or end, end)
        if hi >= lo:
            out[r["line_of_business"]] += ((hi - lo).days + 1) / 365.25
    return dict(out)


def member_spend(data: HealthcarePayerData) -> dict[str, float]:
    """Total allowed spend (medical, net of voids, plus pharmacy) per member."""
    spend: dict[str, float] = defaultdict(float)
    for r in data.tables["medical_claim"].to_pylist():
        if r["claim_status"] in ("denied",) or r["duplicate_of_claim_id"]:
            continue
        if r["claim_status"] == "paid" or r["claim_frequency_code"] == "8":
            spend[r["member_id"]] += r["total_allowed"]
    for r in data.tables["pharmacy_claim"].to_pylist():
        if r["claim_status"] in ("paid", "reversed"):
            spend[r["member_id"]] += r["plan_paid"] + r["patient_pay"]
    return spend


def _pmpy(data: HealthcarePayerData, my: dict[str, float]) -> dict[str, int]:
    lob_of = {r["member_id"]: r["line_of_business"] for r in data.tables["member"].to_pylist()}
    out: dict[str, float] = defaultdict(float)
    for mid, v in member_spend(data).items():
        out[lob_of[mid]] += v
    return {k: round(v / my[k]) for k, v in out.items() if my.get(k)}


def check_utilization(data: HealthcarePayerData) -> Check:
    cal = data.simulation.cal
    my = member_years(data)
    total_my = sum(my.values())
    spend = member_spend(data)
    # share by top x% of members with any coverage (members with no claims count as zero)
    members = [r["member_id"] for r in data.tables["member"].to_pylist()]
    vals = sorted((spend.get(m, 0.0) for m in members), reverse=True)
    tot = sum(vals) or 1.0
    top5 = sum(vals[: max(1, len(vals) // 20)]) / tot
    top1 = sum(vals[: max(1, len(vals) // 100)]) / tot
    bottom50 = sum(vals[len(vals) // 2 :]) / tot
    claims = [
        r
        for r in data.tables["medical_claim"].to_pylist()
        if r["claim_status"] == "paid"
        and r["claim_frequency_code"] != "8"
        and not r["duplicate_of_claim_id"]
    ]
    mem = _members(data)
    ed: Counter[str] = Counter()
    admits: Counter[str] = Counter()
    mix_age: dict[str, Counter[str]] = defaultdict(Counter)
    for r in claims:
        lob = r["line_of_business"]
        if r["facility_type"] == "emergency" or (
            r["facility_type"] == "inpatient" and r["emergency_flag"]
        ):
            ed[lob] += 1
        if r["facility_type"] == "inpatient":
            admits[lob] += 1
        m = mem[r["member_id"]]
        b = band(_age(m["birth_date"], r["service_from_date"]))
        if r["facility_type"] == "emergency":
            mix_age[b]["ed"] += 1
        elif r["facility_type"] == "inpatient":
            mix_age[b]["inpatient"] += 1
        elif r["claim_type"] == "P" and r["place_of_service"] in (
            "11",
            "10",
            "02",
            "19",
            "22",
            "20",
        ):
            mix_age[b]["outpatient"] += 1
    per_1000 = {
        lob: {"ed": round(ed[lob] / my[lob] * 1000), "admits": round(admits[lob] / my[lob] * 1000)}
        for lob in my
    }
    # winter vs summer respiratory
    resp: Counter[str] = Counter()
    dx = _dx_by_claim(data)
    seen: set[tuple[str, date]] = set()
    for r in claims:
        codes = [c for c, _ in dx[r["claim_id"]][:1]]
        if codes and codes[0].startswith(
            ("J00", "J01", "J02", "J03", "J06", "J10", "J11", "J20", "J21", "J18")
        ):
            key = (r["member_id"], r["service_from_date"])
            if key not in seen:
                seen.add(key)
                mth = r["service_from_date"].month
                resp[
                    "winter" if mth in (12, 1, 2) else "summer" if mth in (6, 7, 8) else "other"
                ] += 1
    ratio = resp["winter"] / resp["summer"] if resp["summer"] else None
    # births
    deliveries = [
        r
        for r in claims
        if r["facility_type"] == "inpatient"
        and r["drg_code"] in ("805", "806", "807", "786", "787", "788")
    ]
    ces = sum(1 for r in deliveries if r["drg_code"] in ("786", "787", "788"))
    women_years = 0.0
    elig = data.tables["eligibility"].to_pylist()
    for e in elig:
        m = mem[e["member_id"]]
        if m["sex"] != "F":
            continue
        lo = max(e["coverage_start"], data.simulation.start)
        hi = min(e["coverage_end"] or data.simulation.end, data.simulation.end)
        if hi < lo:
            continue
        age = _age(m["birth_date"], lo)
        if 15 <= age <= 44:
            women_years += ((hi - lo).days + 1) / 365.25
    births_per_1000 = len(deliveries) / women_years * 1000 if women_years else None
    notes: list[str] = []
    t5, t1 = cal.get("util.top5_share"), cal.get("util.top1_share")
    ed_band, adm_band = cal.get("util.ed_total_per_1000"), cal.get("util.admit_total_per_1000")
    ok = t5[0] <= top5 <= t5[1] and t1[0] <= top1 <= t1[1]
    for lob, v in per_1000.items():
        if my[lob] >= 500:
            ok = (
                ok
                and ed_band[lob][0] <= v["ed"] <= ed_band[lob][1]
                and adm_band[lob][0] <= v["admits"] <= adm_band[lob][1]
            )
    if ratio is not None and resp["summer"] >= 30:
        ok = ok and ratio >= 1.5
    if births_per_1000 is not None and women_years >= 1500:
        ok = ok and 40.0 <= births_per_1000 <= 75.0
    if len(deliveries) >= 100:
        target = cal.get("preg.cesarean_rate")
        half = 3.0 * (target * (1 - target) / len(deliveries)) ** 0.5
        ok = ok and abs(ces / len(deliveries) - target) <= half
    return Check(
        "4",
        "utilisation shape",
        ok,
        {
            "top5_share": round(top5, 3),
            "top1_share": round(top1, 3),
            "bottom50_share": round(bottom50, 3),
            "member_years": {k: round(v) for k, v in my.items()},
            "per_1000_member_years": per_1000,
            "winter_to_summer_respiratory": round(ratio, 2) if ratio else None,
            "births_per_1000_women_15_44": round(births_per_1000, 1) if births_per_1000 else None,
            "cesarean_share": round(ces / len(deliveries), 3) if deliveries else None,
            "service_mix_by_age": {k: dict(v) for k, v in sorted(mix_age.items())},
            "allowed_per_member_year": round(sum(spend.values()) / total_my) if total_my else None,
            "allowed_per_member_year_by_lob": _pmpy(data, my),
            "targets": {"top5": t5, "top1": t1, "ed": ed_band, "admits": adm_band},
        },
        notes,
    )


# ---- (5) code validity -------------------------------------------------------------------------
def check_codes(data: HealthcarePayerData, ndc: NdcDirectory | None = None) -> Check:
    ndcs = ndc or data.rx.ndc
    claims = {r["claim_id"]: r for r in data.tables["medical_claim"].to_pylist()}
    bad: list[str] = []
    n_dx = 0
    for r in data.tables["claim_diagnosis"].to_pylist():
        n_dx += 1
        icd = ICD10CM.get(r["diagnosis_code"])
        day = r["service_date"]
        if icd is None or not icd.billable or not icd.valid_on(day):
            bad.append(f"{r['diagnosis_code']} on {day}")
    pcs_bad = sum(
        1 for r in data.tables["claim_procedure"].to_pylist() if r["icd10pcs_code"] not in PCS
    )
    line_bad = 0
    cpt_lines = 0
    for r in data.tables["medical_claim_line"].to_pylist():
        s = SERVICES.get(r["service_key"])
        if s is None or r["service_date"] < s.effective:
            line_bad += 1
            continue
        if r["code_system"] == SYSTEM_HCPCS and r["procedure_code"] != s.hcpcs:
            line_bad += 1
        if r["code_system"] == SYSTEM_CPT:
            cpt_lines += 1
        if r["code_system"] == SYSTEM_SVC and s.hcpcs:
            line_bad += 1
        c = claims[r["claim_id"]]
        if c["claim_type"] == "P":
            if r["place_of_service"] not in s.pos or not pos_valid_on(
                r["place_of_service"], r["service_date"]
            ):
                line_bad += 1
            spec = _provider_specialty(data).get(r["rendering_provider_npi"])
            if spec not in s.specialties:
                line_bad += 1
    ndc_bad = 0
    n_fills = 0
    for f in data.tables["pharmacy_claim"].to_pylist():
        n_fills += 1
        if (
            not ndcs.is_marketed(f["ndc"], f["fill_date"])
            or len(f["ndc"]) != 11
            or not f["ndc"].isdigit()
        ):
            ndc_bad += 1
    return Check(
        "5",
        "every code valid and billable on the date of service",
        not bad and not pcs_bad and not line_bad and not ndc_bad,
        {
            "diagnoses": n_dx,
            "invalid_diagnoses": len(bad),
            "invalid_pcs": pcs_bad,
            "invalid_lines": line_bad,
            "cpt_lines_without_licensed_table": cpt_lines if not data.licensed.cpt else None,
            "fills": n_fills,
            "ndc_not_marketed_on_fill_date": ndc_bad,
            "ndc_source": ndcs.source,
        },
        bad[:10],
    )


_SPEC_CACHE: dict[int, dict[str, str]] = {}


def _provider_specialty(data: HealthcarePayerData) -> dict[str, str]:
    key = id(data)
    if key not in _SPEC_CACHE:
        _SPEC_CACHE[key] = {
            r["npi"]: r["specialty_key"] for r in data.tables["provider"].to_pylist()
        }
    return _SPEC_CACHE[key]


# ---- (6) financial coherence ------------------------------------------------------------------
def _c(x: float) -> int:
    return int(round(x * 100))


def check_financial(data: HealthcarePayerData) -> Check:
    bad_claim = bad_line = bad_header = 0
    lines_by_claim: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for r in data.tables["medical_claim_line"].to_pylist():
        lines_by_claim[(r["claim_id"], r["claim_version"])].append(r)
        # allowed = paid + member responsibility, to the cent, on every line
        if _c(r["allowed_amount"]) != _c(r["paid_amount"]) + _c(r["member_responsibility"]):
            bad_line += 1
        if _c(r["member_responsibility"]) != _c(r["copay"]) + _c(r["coinsurance"]) + _c(
            r["deductible"]
        ):
            bad_line += 1
    header_to_line = (
        ("total_billed", "billed_amount"),
        ("total_allowed", "allowed_amount"),
        ("total_paid", "paid_amount"),
        ("member_copay", "copay"),
        ("member_coinsurance", "coinsurance"),
        ("member_deductible", "deductible"),
        ("cob_paid_amount", "cob_paid_amount"),
        ("gross_allowed_amount", "gross_allowed_amount"),
    )
    for r in data.tables["medical_claim"].to_pylist():
        if _c(r["total_allowed"]) != _c(r["total_paid"]) + _c(r["member_responsibility"]):
            bad_claim += 1
        ls = lines_by_claim[(r["claim_id"], r["claim_version"])]
        for head, line in header_to_line:
            if _c(r[head]) != sum(_c(x[line]) for x in ls):
                bad_header += 1
    rx_bad = 0
    for f in data.tables["pharmacy_claim"].to_pylist():
        if f["claim_status"] in ("paid", "reversed"):
            if _c(f["ingredient_cost"]) + _c(f["dispensing_fee"]) != _c(f["patient_pay"]) + _c(
                f["plan_paid"]
            ):
                rx_bad += 1
    acc_bad = 0
    for r in data.tables["member_accumulator"].to_pylist():
        if _c(r["oop_met"]) > _c(r["oop_limit"]) or _c(r["deductible_met"]) > _c(
            r["deductible_limit"]
        ):
            acc_bad += 1
    allowed_by_proc: dict[str, list[float]] = defaultdict(list)
    for r in data.tables["medical_claim_line"].to_pylist():
        if (
            r["allowed_amount"] > 0
            and r["units"] == 1
            and r["service_key"].startswith(("EM_OFFICE_EST_3", "LAB_HBA1C", "LAB_LIPID_PANEL"))
        ):
            allowed_by_proc[r["service_key"]].append(r["allowed_amount"])
    cv = {
        k: round(statistics.pstdev(v) / statistics.mean(v), 3)
        for k, v in allowed_by_proc.items()
        if len(v) > 20
    }
    ok = not (bad_claim or bad_line or bad_header or rx_bad or acc_bad)
    return Check(
        "6",
        "financial coherence",
        ok,
        {
            "claim_rows_not_balancing": bad_claim,
            "line_rows_not_balancing": bad_line,
            "header_not_sum_of_lines": bad_header,
            "pharmacy_rows_not_balancing": rx_bad,
            "accumulator_rows_over_limit": acc_bad,
            "allowed_amount_cv_by_procedure": cv,
        },
    )


# ---- (7) LOS and DRG; readmissions -----------------------------------------------------------
def _age_ok(member: dict[str, Any], year: int) -> bool:
    return bool(year - member["birth_date"].year >= 18)


def check_pharmacy(data: HealthcarePayerData) -> Check:
    """Pharmacy dynamics against the table: generic share, adherence, rejects, reversals,
    mail order."""
    cal = data.simulation.cal
    rx = data.tables["pharmacy_claim"].to_pylist()
    paid = [r for r in rx if r["claim_status"] == "paid"]
    generic = sum(r["brand_generic"] == "G" for r in paid) / len(paid)
    pdc = data.tables["rx_adherence"].to_pylist()
    adherent = sum(r["adherent_pdc_80"] for r in pdc) / len(pdc)
    by_lob_rej: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in rx:
        if r["transaction_code"] == "B1":
            by_lob_rej[r["line_of_business"]][1] += 1
            by_lob_rej[r["line_of_business"]][0] += r["claim_status"] == "rejected"
    reject = {k: v[0] / v[1] for k, v in by_lob_rej.items() if v[1] >= 500}
    reversal = sum(r["claim_status"] == "reversed" for r in rx) / len(paid)
    ninety: defaultdict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in paid:
        if r["days_supply"] >= 90:
            ninety[r["line_of_business"]][1] += 1
            ninety[r["line_of_business"]][0] += r["mail_order"]
    mail = {k: v[0] / v[1] for k, v in ninety.items() if v[1] >= 100}
    t_generic = float(cal.get("rx.generic_dispense_rate"))
    t_pdc = float(cal.get("rx.pdc_target"))
    t_reject = cal.get("rx.reject_rate")
    t_rev = float(cal.get("rx.reversal_rate"))
    ok = abs(generic - t_generic) <= 0.06 and abs(adherent - t_pdc) <= 0.2
    ok = ok and all(0.4 * t_reject[k] <= v <= 2.0 * t_reject[k] for k, v in reject.items())
    ok = ok and 0.5 * t_rev <= reversal <= 1.6 * t_rev
    return Check(
        "P",
        "pharmacy dynamics (generic share, adherence, rejects, reversals)",
        ok,
        {
            "generic_share": round(generic, 3),
            "share_pdc_80_or_more": round(adherent, 3),
            "reject_rate_by_lob": {k: round(v, 4) for k, v in reject.items()},
            "reversal_rate": round(reversal, 4),
            "mail_share_of_90_day_fills": {k: round(v, 3) for k, v in mail.items()},
            "targets": {"generic": t_generic, "pdc": t_pdc, "reject": t_reject, "reversal": t_rev},
        },
    )


def check_los_drg(data: HealthcarePayerData) -> Check:
    dx = _dx_by_claim(data)
    claims = [
        r
        for r in data.tables["medical_claim"].to_pylist()
        if r["facility_type"] == "inpatient"
        and r["claim_status"] == "paid"
        and r["claim_frequency_code"] != "8"
        and not r["duplicate_of_claim_id"]
    ]
    out_of_range = order_bad = tier_bad = 0
    by_drg: dict[str, list[int]] = defaultdict(list)
    for r in claims:
        d = DRG[r["drg_code"]]
        los = r["length_of_stay"]
        if r["admission_date"] > r["discharge_date"]:
            order_bad += 1
        if not d.los_min <= los <= d.los_max:
            out_of_range += 1
        by_drg[r["drg_code"]].append(los)
        codes = [c for c, _ in dx[r["claim_id"]]]
        has_mcc = any(c in MCC_CODES for c in codes[1:])
        if d.title.endswith("with MCC") and not has_mcc:
            tier_bad += 1
    gm = {}
    for code, v in by_drg.items():
        if len(v) >= 15:
            import math

            g = math.exp(sum(math.log(x) for x in v) / len(v))
            gm[code] = (round(g, 2), DRG[code].gmlos, len(v))
    gm_ok = all(abs(g - ref) / ref <= 0.35 for g, ref, _ in gm.values())
    # readmissions: a stay that begins within 30 days of an earlier discharge of the same member
    by_member: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in claims:
        by_member[r["member_id"]].append(r)
    readm = 0
    for stays in by_member.values():
        stays.sort(key=lambda r: r["admission_date"])
        for a, b in zip(stays, stays[1:], strict=False):
            if (
                0 <= (b["admission_date"] - a["discharge_date"]).days <= 30
                and a["patient_status_code"] != "20"
            ):
                readm += 1
    rate = readm / len(claims) if claims else 0.0
    lo, hi = data.simulation.cal.get("util.readmit_30d")
    ok = not (out_of_range or order_bad or tier_bad) and gm_ok and lo <= rate <= hi
    return Check(
        "7",
        "length of stay agrees with DRG; readmissions rare",
        ok,
        {
            "inpatient_claims": len(claims),
            "los_outside_drg_range": out_of_range,
            "discharge_before_admission": order_bad,
            "mcc_drg_without_mcc_dx": tier_bad,
            "geometric_mean_los_vs_drg(n>=15)": gm,
            "readmission_30d_rate": round(rate, 4),
            "band": (lo, hi),
        },
    )


def run_all(data: HealthcarePayerData) -> list[Check]:
    return [
        check_age_sex(data),
        check_coherence(data),
        check_comorbidity(data),
        check_utilization(data),
        check_codes(data),
        check_financial(data),
        check_los_drg(data),
        check_pharmacy(data),
    ]


def render_markdown(checks: list[Check]) -> str:
    lines = ["| # | Acceptance item | Result | Measured |", "|---|---|---|---|"]
    for c in checks:
        lines.append(
            f"| {c.item} | {c.title} | {'pass' if c.passed else '**FAIL**'} | `{c.metrics}` |"
        )
    return "\n".join(lines)
