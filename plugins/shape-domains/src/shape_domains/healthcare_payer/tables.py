"""Arrow tables of the healthcare payer domain.

Money is ``float64`` rounded to cents with ``precision`` 12 and ``scale`` 2 recorded in the field
metadata (owner decision on issue #24: decimal columns keep the float dtype).  Identifiers are
strings so leading zeros (ZIP, NDC, NPI) survive.  Every claim row is one *version* of a claim:
``claim_root_id`` ties the original, its corrected, adjusted, voided and re-billed versions.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]

from .byo import LicensedTables
from .claims import ClaimsBuilder, Draft
from .drugs import DRUGS, InterimNdcDirectory, format_ndc
from .icd10cm import ICD10CM
from .model import Member
from .reference import CARC, DRG, HCC_LABEL, NCPDP_REJECT, RARC, SPECIALTY
from .risk import hccs_for, raf
from .rx import GROUP_OF, RxBuilder
from .services import code_for

_MONEY = {"precision": "12", "scale": "2"}


def money(name: str) -> pa.Field:
    return pa.field(name, pa.float64(), metadata=_MONEY)


S, I, D, B, F = pa.string(), pa.int32(), pa.date32(), pa.bool_(), pa.float64()


def _table(schema: pa.Schema, rows: list[dict[str, Any]]) -> pa.Table:
    cols = {f.name: [r.get(f.name) for r in rows] for f in schema}
    return pa.table(cols, schema=schema)


def dollars(cents: int) -> float:
    return round(cents / 100.0, 2)


# ---------------------------------------------------------------------------------------------
def member_tables(members: list[Member], plans: dict[str, Any], directory: Any) -> dict[str, pa.Table]:
    mschema = pa.schema([
        ("member_id", S), ("subscriber_id", S), ("member_suffix", S), ("relationship_code", S),
        ("first_name", S), ("last_name", S), ("sex", S), ("birth_date", D), ("death_date", D),
        ("ssn", S), ("email", S), ("phone", S), ("address_line_1", S), ("city", S), ("county", S),
        ("state", S), ("zip", S), ("latitude", F), ("longitude", F), ("line_of_business", S),
        ("household_id", I), ("group_id", S), ("pcp_npi", S), ("pcp_attribution", S),
        ("cob_flag", B), ("dual_eligible", B),
    ])
    rows = []
    for m in members:
        plan = plans[m.spans[0].plan_id] if m.spans else None
        rows.append({
            "member_id": m.member_id, "subscriber_id": m.subscriber_id, "member_suffix": m.suffix,
            "relationship_code": m.relationship, "first_name": m.first, "last_name": m.last, "sex": m.sex,
            "birth_date": m.dob, "death_date": m.death, "ssn": m.ssn, "email": m.email, "phone": m.phone,
            "address_line_1": m.street, "city": m.city, "county": None, "state": m.state, "zip": m.zip,
            "latitude": round(m.lat, 6), "longitude": round(m.lon, 6), "line_of_business": m.lob,
            "household_id": m.household, "group_id": m.group_id,
            "pcp_npi": directory.info(m.pcp).npi if m.pcp is not None else None,
            "pcp_attribution": "assigned" if plan is not None and plan.pcp_required else "attributed",
            "cob_flag": m.cob, "dual_eligible": m.dual,
        })
    eschema = pa.schema([
        ("member_id", S), ("subscriber_id", S), ("plan_id", S), ("line_of_business", S), ("product", S),
        ("group_id", S), ("effective_date", D), ("termination_date", D), ("termination_reason", S),
        ("gap_before_days", I), ("pcp_npi", S), ("cob_flag", B), ("relationship_code", S),
        ("span_number", I),
    ])
    erows = []
    for m in members:
        prev_end: date | None = None
        for n, s in enumerate(sorted(m.spans, key=lambda s: s.start), start=1):
            erows.append({
                "member_id": m.member_id, "subscriber_id": m.subscriber_id, "plan_id": s.plan_id,
                "line_of_business": m.lob, "product": plans[s.plan_id].product, "group_id": m.group_id,
                "effective_date": s.start, "termination_date": s.end, "termination_reason": s.reason,
                "gap_before_days": (s.start - prev_end).days - 1 if prev_end else None,
                "pcp_npi": directory.info(m.pcp).npi if m.pcp is not None else None,
                "cob_flag": m.cob, "relationship_code": m.relationship, "span_number": n,
            })
            prev_end = s.end
    pschema = pa.schema([
        ("plan_id", S), ("line_of_business", S), ("product", S), money("deductible"), money("oop_max"),
        ("coinsurance_rate", F), money("copay_pcp"), money("copay_specialist"), money("copay_ed"),
        money("copay_urgent"), money("inpatient_copay_per_day"), ("family_multiple", F),
    ])
    prow = [{
        "plan_id": p.plan_id, "line_of_business": p.lob, "product": p.product, "deductible": p.deductible,
        "oop_max": p.oop_max, "coinsurance_rate": p.coinsurance, "copay_pcp": p.copay_pcp,
        "copay_specialist": p.copay_specialist, "copay_ed": p.copay_ed, "copay_urgent": p.copay_urgent,
        "inpatient_copay_per_day": p.inpatient_copay_day, "family_multiple": p.family_multiple,
    } for p in plans.values()]
    return {"member": _table(mschema, rows), "eligibility": _table(eschema, erows), "plan": _table(pschema, prow)}


def provider_table(directory: Any) -> pa.Table:
    schema = pa.schema([
        ("npi", S), ("name", S), ("entity_type", S), ("specialty", S), ("specialty_name", S),
        ("taxonomy_code", S), ("state", S), ("zip", S), ("city", S), ("latitude", F), ("longitude", F),
        ("network_status", S), ("billing_group_npi", S), ("is_facility", B), ("provider_kind", S),
    ])
    rows = []
    for p in directory.providers:
        spec = SPECIALTY[p.specialty]
        rows.append({
            "npi": p.npi, "name": p.name, "entity_type": "1" if p.kind == "individual" else "2",
            "specialty": p.specialty, "specialty_name": spec.name, "taxonomy_code": p.taxonomy,
            "state": p.state, "zip": p.zip, "city": p.city, "latitude": round(p.lat, 6),
            "longitude": round(p.lon, 6), "network_status": "in-network" if p.network else "out-of-network",
            "billing_group_npi": p.billing_npi, "is_facility": p.specialty in ("hospital", "dialysis_center", "urgent_care"),
            "provider_kind": "pharmacy" if "pharmacy" in p.specialty else p.kind,
        })
    return _table(schema, rows)


# ---------------------------------------------------------------------------------------------
CLAIM_SCHEMA = pa.schema([
    ("claim_id", S), ("claim_root_id", S), ("claim_version", I), ("replaces_claim_id", S),
    ("duplicate_of_claim_id", S), ("claim_frequency_code", S), ("claim_type", S), ("claim_status", S),
    ("member_id", S), ("subscriber_id", S), ("plan_id", S), ("line_of_business", S),
    ("service_from_date", D), ("service_to_date", D), ("received_date", D), ("adjudication_date", D),
    ("paid_date", D), ("facility_type", S), ("type_of_bill", S), ("place_of_service", S),
    ("admission_date", D), ("discharge_date", D), ("length_of_stay", I), ("patient_discharge_status", S),
    ("drg_code", S), ("emergency_flag", B), ("readmission_of_claim_root", S),
    ("billing_provider_npi", S), ("rendering_provider_npi", S), ("facility_npi", S),
    ("network_status", S), ("prior_auth_number", S), ("encounter_id", I),
    money("billed_amount"), money("allowed_amount"), money("paid_amount"), money("copay_amount"),
    money("coinsurance_amount"), money("deductible_amount"), money("member_responsibility"),
    money("cob_paid_amount"), ("denial_carc", S), ("denial_rarc", S), ("denial_description", S),
    ("module", S),
])
LINE_SCHEMA = pa.schema([
    ("claim_id", S), ("claim_version", I), ("line_number", I), ("service_date", D), ("code_system", S),
    ("procedure_code", S), ("service_key", S), ("modifier_1", S), ("modifier_2", S), ("units", I),
    ("revenue_code", S), ("place_of_service", S), ("rendering_provider_npi", S), ("diagnosis_pointers", S),
    money("billed_amount"), money("allowed_amount"), money("paid_amount"), money("copay_amount"),
    money("coinsurance_amount"), money("deductible_amount"), money("member_responsibility"),
    money("cob_paid_amount"), ("line_status", S), ("denial_carc", S), ("denial_rarc", S),
])
DX_SCHEMA = pa.schema([
    ("claim_id", S), ("claim_version", I), ("sequence", I), ("code_system", S), ("diagnosis_code", S),
    ("present_on_admission", S), ("diagnosis_role", S), ("service_date", D),
])
PROC_SCHEMA = pa.schema([("claim_id", S), ("claim_version", I), ("sequence", I), ("code_system", S),
                         ("procedure_code", S), ("procedure_date", D)])
AUTH_SCHEMA = pa.schema([
    ("authorization_id", S), ("member_id", S), ("plan_id", S), ("service_key", S), ("service_code", S),
    ("requested_date", D), ("decision_date", D), ("status", S), ("valid_from", D), ("valid_to", D),
    ("units_authorized", I), ("retroactive", B),
])


def claim_tables(cb: ClaimsBuilder, lic: LicensedTables) -> dict[str, pa.Table]:
    claims: list[dict[str, Any]] = []
    lines: list[dict[str, Any]] = []
    dxs: list[dict[str, Any]] = []
    procs: list[dict[str, Any]] = []
    drafts = sorted(cb.drafts, key=lambda d: (d.frm, d.n))
    roots = {d.n: f"CLM{k:010d}" for k, d in enumerate(drafts, start=1)}
    stay_root = {d.enc.eid: roots[d.n] for d in drafts if d.facility_type == "inpatient"}
    auth_by_draft: dict[int, str] = {}
    for a in cb.auths:
        auth_by_draft.setdefault(a.draft, a.auth_id)
    for d in drafts:
        root = roots[d.n]
        prev_id: str | None = None
        readmit = stay_root.get(d.enc.readmit_of) if d.enc.readmit_of and d.facility_type == "inpatient" else None
        for v in d.versions:
            cid = root if v["version"] == 1 else f"{root}-{v['version']}"
            _emit_version(cb, lic, d, v, cid, root, prev_id, auth_by_draft, claims, lines, dxs, procs,
                          readmit=readmit)
            prev_id = cid
        if _dup(cb, d):
            first = d.versions[0]
            received = first["received"] + timedelta(days=3)
            dup = {"version": 1, "freq": "1", "status": "denied", "denial": ("18", "N522"), "received": received,
                   "adjudicated": received + timedelta(days=5), "paid_date": None, "kind": "duplicate"}
            _emit_version(cb, lic, d, dup, f"{root}-D1", f"{root}-D1", None, auth_by_draft, claims, lines, dxs,
                          procs, duplicate_of=root)
    return {
        "medical_claim": _table(CLAIM_SCHEMA, claims),
        "medical_claim_line": _table(LINE_SCHEMA, lines),
        "claim_diagnosis": _table(DX_SCHEMA, dxs),
        "claim_procedure": _table(PROC_SCHEMA, procs),
        "prior_authorization": _table(AUTH_SCHEMA, [{
            "authorization_id": a.auth_id, "member_id": f"SYN{a.member + 1:09d}", "plan_id": a.plan_id,
            "service_key": a.service_key, "service_code": a.code, "requested_date": a.requested,
            "decision_date": a.decided, "status": a.status, "valid_from": a.valid_from, "valid_to": a.valid_to,
            "units_authorized": a.units, "retroactive": a.retro,
        } for a in cb.auths]),
    }


def _dup(cb: ClaimsBuilder, d: Draft) -> bool:
    rng = np.random.default_rng([cb.seed, 71, d.n])
    return bool(rng.random() < cb.cal.get("claim.duplicate_share")) and d.versions[0]["status"] != "denied"


def _amounts(d: Draft, v: dict[str, Any]) -> tuple[list[tuple[int, int, int, int, int, int, int]], str]:
    """Per-line (billed, allowed, paid, copay, coins, ded, cob) for a version, and its status."""
    kind = v["kind"]
    out = []
    for ln in d.lines:
        if v["status"] == "denied" or kind == "duplicate":
            out.append((ln.billed, 0, 0, 0, 0, 0, 0))
        elif kind == "void":
            out.append((-ln.billed, -ln.allowed, -ln.paid, -ln.copay, -ln.coins, -ln.ded, -ln.cob))
        elif kind == "adjusted":
            scale = v["scale"]
            allowed = int(round(ln.allowed * scale))
            member = ln.copay + ln.coins + ln.ded
            paid = max(0, allowed - member - ln.cob)
            allowed = paid + member + ln.cob
            out.append((ln.billed, allowed, paid, ln.copay, ln.coins, ln.ded, ln.cob))
        else:
            out.append((ln.billed, ln.allowed, ln.paid, ln.copay, ln.coins, ln.ded, ln.cob))
    return out, v["status"]


def _emit_version(cb: ClaimsBuilder, lic: LicensedTables, d: Draft, v: dict[str, Any], cid: str, root: str,
                  prev: str | None, auth_by_draft: dict[int, str], claims: list[dict[str, Any]],
                  lines: list[dict[str, Any]], dxs: list[dict[str, Any]], procs: list[dict[str, Any]],
                  duplicate_of: str | None = None, readmit: str | None = None) -> None:
    amounts, status = _amounts(d, v)
    tot = [sum(a[i] for a in amounts) for i in range(7)]
    denial = v.get("denial")
    carc = denial[0] if denial else None
    rarc = denial[1] if denial else None
    enc = d.enc
    inst = d.ctype == "I"
    key = f"{d.facility_type}:{v['freq']}"
    rendering = d.rendering.npi if d.rendering is not None else None
    claims.append({
        "claim_id": cid, "claim_root_id": root, "claim_version": v["version"], "replaces_claim_id": prev,
        "duplicate_of_claim_id": duplicate_of, "claim_frequency_code": v["freq"], "claim_type": d.ctype,
        "claim_status": ("denied" if status == "denied" else status) if not duplicate_of else "denied",
        "member_id": d.member.member_id, "subscriber_id": d.member.subscriber_id, "plan_id": d.plan.plan_id,
        "line_of_business": d.plan.lob, "service_from_date": d.frm, "service_to_date": d.to,
        "received_date": v["received"], "adjudication_date": v["adjudicated"], "paid_date": v["paid_date"],
        "facility_type": d.facility_type, "type_of_bill": lic.tob.get(key) if inst else None,
        "place_of_service": d.pos, "admission_date": enc.admit if d.facility_type == "inpatient" else None,
        "discharge_date": enc.discharge if d.facility_type == "inpatient" else None,
        "length_of_stay": d.inpatient_days if d.facility_type == "inpatient" else None,
        "patient_discharge_status": d.status_code, "drg_code": d.drg, "emergency_flag": d.emergency,
        "readmission_of_claim_root": readmit, "billing_provider_npi": d.billing.npi, "rendering_provider_npi": rendering,
        "facility_npi": d.billing.npi if inst else None,
        "network_status": "in-network" if d.billing.network else "out-of-network",
        "prior_auth_number": auth_by_draft.get(d.n), "encounter_id": enc.eid,
        "billed_amount": dollars(tot[0]), "allowed_amount": dollars(tot[1]), "paid_amount": dollars(tot[2]),
        "copay_amount": dollars(tot[3]), "coinsurance_amount": dollars(tot[4]), "deductible_amount": dollars(tot[5]),
        "member_responsibility": dollars(tot[3] + tot[4] + tot[5]), "cob_paid_amount": dollars(tot[6]),
        "denial_carc": carc, "denial_rarc": rarc,
        "denial_description": (CARC[carc].desc if carc else None), "module": enc.module,
    })
    for i, (ln, a) in enumerate(zip(d.lines, amounts, strict=True), start=1):
        sys_, code = code_for(ln.svc, lic.cpt)
        mods = list(ln.modifiers) + [None, None]
        lines.append({
            "claim_id": cid, "claim_version": v["version"], "line_number": i, "service_date": ln.day,
            "code_system": sys_, "procedure_code": code, "service_key": ln.svc.key,
            "modifier_1": mods[0], "modifier_2": mods[1], "units": ln.units,
            "revenue_code": lic.revenue.get(ln.svc.key) if inst else None,
            "place_of_service": d.pos, "rendering_provider_npi": ln.rendering.npi if ln.rendering else None,
            "diagnosis_pointers": ",".join(str(p) for p in ln.dx_ptr) if not inst else None,
            "billed_amount": dollars(a[0]), "allowed_amount": dollars(a[1]), "paid_amount": dollars(a[2]),
            "copay_amount": dollars(a[3]), "coinsurance_amount": dollars(a[4]), "deductible_amount": dollars(a[5]),
            "member_responsibility": dollars(a[3] + a[4] + a[5]), "cob_paid_amount": dollars(a[6]),
            "line_status": "denied" if (status == "denied" or duplicate_of) else "reversed" if v["kind"] == "void" else "paid",
            "denial_carc": carc, "denial_rarc": rarc,
        })
    for n, (code, poa) in enumerate(d.dx, start=1):
        dxs.append({"claim_id": cid, "claim_version": v["version"], "sequence": n, "code_system": "ICD-10-CM",
                    "diagnosis_code": code, "present_on_admission": poa if d.facility_type == "inpatient" else None,
                    "diagnosis_role": "principal" if n == 1 else "secondary", "service_date": d.frm})
    for n, code in enumerate(d.pcs, start=1):
        procs.append({"claim_id": cid, "claim_version": v["version"], "sequence": n, "code_system": "ICD-10-PCS",
                      "procedure_code": code, "procedure_date": d.frm})


# ---------------------------------------------------------------------------------------------
RX_SCHEMA = pa.schema([
    ("rx_claim_id", S), ("member_id", S), ("plan_id", S), ("line_of_business", S), ("fill_date", D),
    ("date_written", D), ("rx_number", S), ("fill_number", I), ("transaction_code", S), ("claim_status", S),
    ("reject_code", S), ("reject_description", S), ("ndc", S), ("drug_name", S), ("brand_name", S),
    ("strength", S), ("dosage_form", S), ("route", S), ("therapeutic_class", S), ("dea_schedule", S),
    ("brand_generic", S), ("quantity_dispensed", F), ("days_supply", I), ("daw_code", S),
    ("prescriber_npi", S), ("pharmacy_npi", S), ("pharmacy_ncpdp_id", S), ("pharmacy_type", S),
    ("formulary_tier", I), money("ingredient_cost"), money("dispensing_fee"), money("patient_pay"),
    money("plan_paid"), ("rx_order_id", I), ("early_refill_flag", B), ("primary_fill_flag", B),
])
ORDER_SCHEMA = pa.schema([
    ("rx_order_id", I), ("member_id", S), ("date_written", D), ("drug_name", S), ("strength", S), ("quantity", F),
    ("days_supply", I), ("refills_authorized", I), ("daw_code", S), ("prescriber_npi", S),
    ("indication", S), ("indication_icd10cm", S), ("source_encounter_id", I), ("acute_flag", B),
])
NDC_SCHEMA = pa.schema([
    ("ndc", S), ("ndc_formatted", S), ("drug_key", S), ("drug_name", S), ("brand_name", S), ("strength", S),
    ("dosage_form", S), ("route", S), ("therapeutic_class", S), ("dea_schedule", S), ("brand_generic", S),
    ("package_units", F), ("marketing_start_date", D), ("marketing_end_date", D), ("formulary_tier", I),
    ("source", S),
])
ADH_SCHEMA = pa.schema([
    ("member_id", S), ("measurement_year", I), ("therapeutic_group", S), ("first_fill_date", D),
    ("period_days", I), ("days_covered", I), ("pdc", F), ("adherent_pdc_80", B), ("fills", I),
    ("gap_days", I), ("max_gap_days", I), ("early_refills", I), ("abandoned_flag", B),
])


def rx_tables(rb: RxBuilder) -> dict[str, pa.Table]:
    rows = []
    for k, f in enumerate(rb.fills, start=1):
        d = f.order.drug
        pharm = rb.dir.info(f.pharmacy)
        sign = -1 if f.status == "reversed" else 1
        rows.append({
            "rx_claim_id": f"RXC{k:010d}", "member_id": f.member.member_id, "plan_id": f.plan.plan_id,
            "line_of_business": f.plan.lob, "fill_date": f.day, "date_written": f.order.written,
            "rx_number": f"{f.order.oid:08d}", "fill_number": f.fill_number, "transaction_code": f.txn,
            "claim_status": f.status, "reject_code": f.reject,
            "reject_description": NCPDP_REJECT[f.reject] if f.reject else None, "ndc": f.ndc,
            "drug_name": d.name, "brand_name": d.brand_name, "strength": d.strength, "dosage_form": d.form,
            "route": d.route, "therapeutic_class": d.cls, "dea_schedule": d.schedule,
            "brand_generic": "B" if d.brand else "G", "quantity_dispensed": f.quantity, "days_supply": f.days_supply,
            "daw_code": f.order.daw, "prescriber_npi": rb.dir.info(f.order.prescriber).npi, "pharmacy_npi": pharm.npi,
            "pharmacy_ncpdp_id": f"9{pharm.idx:06d}", "pharmacy_type": "mail" if f.mail else "retail",
            "formulary_tier": f.tier, "ingredient_cost": dollars(sign * f.ingredient), "dispensing_fee": dollars(sign * f.fee),
            "patient_pay": dollars(sign * f.patient), "plan_paid": dollars(sign * f.paid), "rx_order_id": f.order.oid,
            "early_refill_flag": f.early, "primary_fill_flag": f.primary,
        })
    # reversals and rejects carry the cost of the original paid fill they undo; negate those
    orders = [{
        "rx_order_id": o.oid, "member_id": f"SYN{o.member + 1:09d}", "date_written": o.written, "drug_name": o.drug.name,
        "strength": o.drug.strength, "quantity": o.quantity, "days_supply": o.days_supply, "refills_authorized": o.refills,
        "daw_code": o.daw, "prescriber_npi": rb.dir.info(o.prescriber).npi, "indication": o.indication,
        "indication_icd10cm": o.dx, "source_encounter_id": o.source, "acute_flag": o.acute,
    } for o in rb.orders]
    ndc = InterimNdcDirectory()
    nrows = []
    for rec in sorted(ndc._by_ndc.values(), key=lambda r: r.ndc):  # noqa: SLF001
        d = DRUGS[rec.drug_key]
        nrows.append({
            "ndc": rec.ndc, "ndc_formatted": format_ndc(rec.ndc), "drug_key": d.key, "drug_name": d.name,
            "brand_name": d.brand_name, "strength": d.strength, "dosage_form": d.form, "route": d.route,
            "therapeutic_class": d.cls, "dea_schedule": d.schedule, "brand_generic": "B" if d.brand else "G",
            "package_units": rec.package_units, "marketing_start_date": rec.marketing_start,
            "marketing_end_date": None if rec.marketing_end.year > 9000 else rec.marketing_end,
            "formulary_tier": d.tier, "source": "interim-synthetic" if rec.interim else "FDA NDC Directory",
        })
    adh = [{
        "member_id": f"SYN{r['member_idx'] + 1:09d}", "measurement_year": r["year"], "therapeutic_group": r["therapeutic_group"],
        "first_fill_date": r["first_fill_date"], "period_days": r["period_days"], "days_covered": r["days_covered"],
        "pdc": round(r["pdc"], 4), "adherent_pdc_80": r["pdc"] >= 0.8, "fills": r["fills"], "gap_days": r["gap_days"],
        "max_gap_days": r["max_gap_days"], "early_refills": r["early_refills"], "abandoned_flag": r["abandoned"],
    } for r in rb.adherence_rows()]
    return {
        "pharmacy_claim": _table(RX_SCHEMA, rows), "rx_order": _table(ORDER_SCHEMA, orders),
        "drug_reference": _table(NDC_SCHEMA, nrows), "rx_adherence": _table(ADH_SCHEMA, adh),
    }


# ---------------------------------------------------------------------------------------------
RISK_SCHEMA = pa.schema([
    ("member_id", S), ("risk_year", I), ("raf_prospective", F), ("raf_concurrent", F), ("hcc_prospective", S),
    ("hcc_concurrent", S), ("age_at_year_start", I), ("model", S),
])
ACC_SCHEMA = pa.schema([
    ("member_id", S), ("scope", S), ("plan_year", I), ("plan_id", S), money("deductible_limit"),
    money("deductible_met"), money("oop_limit"), money("oop_met"), money("rx_oop_met"),
])


def risk_table(members: list[Member], cb: ClaimsBuilder, start: date, end: date) -> pa.Table:
    by: dict[tuple[int, int], set[str]] = defaultdict(set)
    for d in cb.drafts:
        if not d.final_paid:
            continue
        for code, _ in d.dx:
            by[(d.member.idx, d.frm.year)].add(code)
    rows = []
    for m in members:
        years = sorted({y for s in m.spans for y in range(max(s.start, start).year, (s.end or end).year + 1)
                        if start.year <= y <= end.year})
        for y in years:
            cur = hccs_for(by.get((m.idx, y), ()))
            prior = hccs_for(by.get((m.idx, y - 1), ())) if y > start.year else None
            age = m.age(date(y, 1, 1))
            rows.append({
                "member_id": m.member_id, "risk_year": y,
                "raf_prospective": raf(age, m.sex, prior) if prior is not None else None,
                "raf_concurrent": raf(age, m.sex, cur),
                "hcc_prospective": ",".join(map(str, prior)) if prior is not None else None,
                "hcc_concurrent": ",".join(map(str, cur)), "age_at_year_start": age,
                "model": "CMS-HCC V28 (seed weights, illustrative)",
            })
    return _table(RISK_SCHEMA, rows)


def accumulator_table(cb: ClaimsBuilder, rb: RxBuilder, members: list[Member], plans: dict[str, Any]) -> pa.Table:
    rows = []
    fam_rows = []
    first_of: dict[int, Member] = {}
    for m in members:
        first_of.setdefault(m.household, m)
    for (idx, year, plan_id), a in sorted(cb.ind.items()):
        p = plans[plan_id]
        rows.append({
            "member_id": members[idx].member_id, "scope": "individual", "plan_year": year, "plan_id": plan_id,
            "deductible_limit": p.deductible, "deductible_met": dollars(a.ded), "oop_limit": p.oop_max,
            "oop_met": dollars(a.oop), "rx_oop_met": dollars(rb.rx_oop.get((idx, year, plan_id), 0)),
        })
    for (hh, year, lob, plan_id), a in sorted(cb.fam.items()):
        p = plans[plan_id]
        sub = first_of.get(hh)
        fam_rows.append({
            "member_id": sub.member_id if sub else None, "scope": "family", "plan_year": year, "plan_id": plan_id,
            "deductible_limit": p.deductible * p.family_multiple, "deductible_met": dollars(a.ded),
            "oop_limit": p.oop_max * p.family_multiple, "oop_met": dollars(a.oop), "rx_oop_met": None,
        })
    return _table(ACC_SCHEMA, rows + fam_rows)
