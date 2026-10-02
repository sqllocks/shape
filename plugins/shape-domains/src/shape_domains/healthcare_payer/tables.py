"""Arrow tables of the healthcare payer domain.

Column names follow the standard-outputs contract (``shape_healthcare_standards.contract``), so the
X12, FHIR and OMOP writers read these tables as they are; extra columns carry what the contract has
no place for (claim versions, lifecycle links, the clinical module that produced a claim).

Money is ``float64`` rounded to cents with ``precision`` 12 and ``scale`` 2 recorded in the field
metadata (owner decision on issue #24: decimal columns keep the float dtype).  Identifiers are
strings so leading zeros (ZIP, NDC, NPI) survive.  Every claim row is one *version* of a claim:
``claim_root_id`` ties the original, its corrected, adjusted, voided and re-billed versions.
ICD-10 codes appear twice on a diagnosis row: ``icd10_code`` without the decimal point (the
claim-file form) and ``diagnosis_code`` with it (the form clinicians read).

Money identity, on every claim and line: ``allowed = paid + member responsibility``.  When another
payer paid first (coordination of benefits), ``allowed`` is the amount left after that payment,
``cob_paid_amount`` is what the other payer paid and ``gross_allowed_amount`` the contracted total.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]

from .byo import LicensedTables
from .claims import ClaimsBuilder, Draft
from .drugs import DRUGS, NdcDirectory, format_ndc
from .model import Member
from .names import STREETS, SUFFIXES
from .reference import CARC, NCPDP_REJECT, SPECIALTY
from .risk import hccs_for, raf
from .rx import RxBuilder
from .services import code_for

_MONEY = {"precision": "12", "scale": "2"}
PLAN_TYPE = {"commercial": "COMMERCIAL", "ma": "MEDICARE_ADVANTAGE", "medicaid": "MEDICAID"}
PAYER = {
    "commercial": ("SYNP1", "Demo Commercial Health Plan"),
    "ma": ("SYNP2", "Demo Medicare Advantage Plan"),
    "medicaid": ("SYNP3", "Demo Medicaid Managed Care"),
}


def money(name: str) -> pa.Field:
    return pa.field(name, pa.float64(), metadata=_MONEY)


S, INT, D, B, F = pa.string(), pa.int32(), pa.date32(), pa.bool_(), pa.float64()


def _table(schema: pa.Schema, rows: list[dict[str, Any]]) -> pa.Table:
    cols = {f.name: [r.get(f.name) for r in rows] for f in schema}
    return pa.table(cols, schema=schema)


def dollars(cents: int) -> float:
    return round(cents / 100.0, 2)


def no_dot(code: str) -> str:
    return code.replace(".", "")


def plan_name(p: Any) -> str:
    base = {"commercial": "Commercial", "ma": "Medicare Advantage", "medicaid": "Medicaid"}[p.lob]
    return f"{base} {p.product} {int(p.deductible)}/{int(p.oop_max)}"


def coverage_level(members: list[Member]) -> dict[int, str]:
    """X12 834 coverage level per household: IND, ESP, ECH, E1D or FAM (subscriber's plan)."""
    by_hh: dict[int, list[Member]] = defaultdict(list)
    for m in members:
        by_hh[m.household].append(m)
    out: dict[int, str] = {}
    for hh, group in by_hh.items():
        deps = [m for m in group if m.relationship != "18"]
        spouse = any(m.relationship == "01" for m in group)
        kids = sum(1 for m in deps if m.relationship == "19")
        if not deps:
            out[hh] = "IND"
        elif len(deps) == 1:
            out[hh] = "ESP" if spouse else "ECH" if kids else "E1D"
        else:
            out[hh] = "FAM" if spouse and kids else "ECH" if kids else "ESP"
    return out


# ---------------------------------------------------------------------------------------------
def member_tables(
    members: list[Member], plans: dict[str, Any], directory: Any
) -> dict[str, pa.Table]:
    mschema = pa.schema(
        [
            ("member_id", S),
            ("subscriber_id", S),
            ("member_suffix", S),
            ("relationship_code", S),
            ("first_name", S),
            ("middle_name", S),
            ("last_name", S),
            ("sex", S),
            ("birth_date", D),
            ("deceased_date", D),
            ("ssn", S),
            ("email", S),
            ("phone", S),
            ("address_line1", S),
            ("address_line2", S),
            ("city", S),
            ("county", S),
            ("state", S),
            ("zip", S),
            ("latitude", F),
            ("longitude", F),
            ("race", S),
            ("ethnicity", S),
            ("language", S),
            ("marital_status", S),
            ("line_of_business", S),
            ("household_id", INT),
            ("group_id", S),
            ("pcp_npi", S),
            ("pcp_attribution", S),
            ("cob_flag", B),
            ("dual_eligible", B),
        ]
    )
    rows = []
    for m in members:
        plan = plans[m.spans[0].plan_id] if m.spans else None
        rows.append(
            {
                "member_id": m.member_id,
                "subscriber_id": m.subscriber_id,
                "member_suffix": m.suffix,
                "relationship_code": m.relationship,
                "first_name": m.first,
                "last_name": m.last,
                "sex": m.sex,
                "birth_date": m.dob,
                "deceased_date": m.death,
                "ssn": m.ssn,
                "email": m.email,
                "phone": m.phone.replace("-", ""),
                "address_line1": m.street,
                "city": m.city,
                "county": None,
                "state": m.state,
                "zip": m.zip,
                "latitude": round(m.lat, 6),
                "longitude": round(m.lon, 6),
                "language": "en",
                "marital_status": "M" if m.relationship in ("01",) else None,
                "line_of_business": m.lob,
                "household_id": m.household,
                "group_id": m.group_id,
                "pcp_npi": directory.info(m.pcp).npi if m.pcp is not None else None,
                "pcp_attribution": "assigned"
                if plan is not None and plan.pcp_required
                else "attributed",
                "cob_flag": m.cob,
                "dual_eligible": m.dual,
            }
        )
    level = coverage_level(members)
    eschema = pa.schema(
        [
            ("eligibility_id", S),
            ("member_id", S),
            ("subscriber_id", S),
            ("plan_id", S),
            ("plan_name", S),
            ("plan_type", S),
            ("line_of_business", S),
            ("product", S),
            ("group_id", S),
            ("group_name", S),
            ("coverage_level", S),
            ("insurance_line", S),
            ("payer_id", S),
            ("payer_name", S),
            ("coverage_start", D),
            ("coverage_end", D),
            ("termination_reason", S),
            ("gap_before_days", INT),
            ("pcp_npi", S),
            ("cob_flag", B),
            ("relationship_code", S),
            ("span_number", INT),
        ]
    )
    erows = []
    for m in members:
        prev_end: date | None = None
        for n, s in enumerate(sorted(m.spans, key=lambda x: x.start), start=1):
            p = plans[s.plan_id]
            payer_id, payer_name = PAYER[m.lob]
            erows.append(
                {
                    "eligibility_id": f"{m.member_id}-{n:02d}",
                    "member_id": m.member_id,
                    "subscriber_id": m.subscriber_id,
                    "plan_id": s.plan_id,
                    "plan_name": plan_name(p),
                    "plan_type": PLAN_TYPE[m.lob],
                    "line_of_business": m.lob,
                    "product": p.product,
                    "group_id": m.group_id,
                    "group_name": f"Group {m.group_id[-4:]}" if m.lob == "commercial" else None,
                    "coverage_level": level[m.household] if m.lob == "commercial" else "IND",
                    "insurance_line": "HLT",
                    "payer_id": payer_id,
                    "payer_name": payer_name,
                    "coverage_start": s.start,
                    "coverage_end": s.end,
                    "termination_reason": s.reason,
                    "gap_before_days": (s.start - prev_end).days - 1 if prev_end else None,
                    "pcp_npi": directory.info(m.pcp).npi if m.pcp is not None else None,
                    "cob_flag": m.cob,
                    "relationship_code": m.relationship,
                    "span_number": n,
                }
            )
            prev_end = s.end
    pschema = pa.schema(
        [
            ("plan_id", S),
            ("plan_name", S),
            ("line_of_business", S),
            ("plan_type", S),
            ("product", S),
            money("deductible"),
            money("oop_max"),
            ("coinsurance_rate", F),
            money("copay_pcp"),
            money("copay_specialist"),
            money("copay_ed"),
            money("copay_urgent"),
            money("inpatient_copay_per_day"),
            ("family_multiple", F),
        ]
    )
    prow = [
        {
            "plan_id": p.plan_id,
            "plan_name": plan_name(p),
            "line_of_business": p.lob,
            "plan_type": PLAN_TYPE[p.lob],
            "product": p.product,
            "deductible": p.deductible,
            "oop_max": p.oop_max,
            "coinsurance_rate": p.coinsurance,
            "copay_pcp": p.copay_pcp,
            "copay_specialist": p.copay_specialist,
            "copay_ed": p.copay_ed,
            "copay_urgent": p.copay_urgent,
            "inpatient_copay_per_day": p.inpatient_copay_day,
            "family_multiple": p.family_multiple,
        }
        for p in plans.values()
    ]
    return {
        "member": _table(mschema, rows),
        "eligibility": _table(eschema, erows),
        "plan": _table(pschema, prow),
    }


def provider_table(directory: Any, lic: LicensedTables | None = None) -> pa.Table:
    taxonomy = lic.taxonomy if lic else {}
    schema = pa.schema(
        [
            ("npi", S),
            ("entity_type", S),
            ("last_name", S),
            ("first_name", S),
            ("org_name", S),
            ("taxonomy_code", S),
            ("specialty", S),
            ("specialty_key", S),
            ("tax_id", S),
            ("address_line1", S),
            ("city", S),
            ("state", S),
            ("zip", S),
            ("latitude", F),
            ("longitude", F),
            ("network_status", S),
            ("ncpdp_id", S),
            ("billing_group_npi", S),
            ("is_facility", B),
            ("provider_kind", S),
        ]
    )
    rows = []
    for p in directory.providers:
        spec = SPECIALTY[p.specialty]
        individual = p.kind == "individual"
        last, _, first = p.name.partition(", ")
        pharmacy = "pharmacy" in p.specialty
        rows.append(
            {
                "npi": p.npi,
                "entity_type": "1" if individual else "2",
                "last_name": last if individual else None,
                "first_name": first if individual else None,
                "org_name": None if individual else p.name,
                "taxonomy_code": taxonomy.get(p.specialty),
                "specialty": spec.name,
                "specialty_key": p.specialty,
                "tax_id": f"00{p.idx:07d}",  # an EIN prefix that is never assigned
                "address_line1": (
                    f"{(p.idx * 37) % 9000 + 100} {STREETS[p.idx % len(STREETS)]} "
                    f"{SUFFIXES[(p.idx // 7) % len(SUFFIXES)]}"
                ),
                "city": p.city,
                "state": p.state,
                "zip": p.zip,
                "latitude": round(p.lat, 6),
                "longitude": round(p.lon, 6),
                "network_status": "IN" if p.network else "OUT",
                "ncpdp_id": f"9{p.idx:06d}" if pharmacy else None,
                "billing_group_npi": p.billing_npi,
                "is_facility": p.specialty in ("hospital", "dialysis_center", "urgent_care"),
                "provider_kind": "pharmacy" if pharmacy else p.kind,
            }
        )
    return _table(schema, rows)


# ---------------------------------------------------------------------------------------------
CLAIM_SCHEMA = pa.schema(
    [
        ("claim_id", S),
        ("claim_root_id", S),
        ("claim_version", INT),
        ("original_claim_id", S),
        ("duplicate_of_claim_id", S),
        ("payer_claim_number", S),
        ("claim_frequency_code", S),
        ("claim_type", S),
        ("claim_status", S),
        ("member_id", S),
        ("subscriber_id", S),
        ("plan_id", S),
        ("payer_id", S),
        ("payer_name", S),
        ("line_of_business", S),
        ("service_from_date", D),
        ("service_to_date", D),
        ("received_date", D),
        ("adjudication_date", D),
        ("paid_date", D),
        ("facility_type", S),
        ("type_of_bill", S),
        ("place_of_service", S),
        ("admission_date", D),
        ("discharge_date", D),
        ("length_of_stay", INT),
        ("admission_type_code", S),
        ("patient_status_code", S),
        ("drg_code", S),
        ("emergency_flag", B),
        ("readmission_of_claim_root", S),
        ("billing_npi", S),
        ("rendering_npi", S),
        ("facility_npi", S),
        ("attending_npi", S),
        ("network_status", S),
        ("prior_auth_number", S),
        ("encounter_id", INT),
        money("total_billed"),
        money("total_allowed"),
        money("total_paid"),
        money("member_copay"),
        money("member_coinsurance"),
        money("member_deductible"),
        money("member_responsibility"),
        money("cob_paid_amount"),
        money("gross_allowed_amount"),
        ("denial_carc", S),
        ("denial_rarc", S),
        ("denial_description", S),
        ("module", S),
    ]
)
LINE_SCHEMA = pa.schema(
    [
        ("claim_id", S),
        ("claim_version", INT),
        ("line_number", INT),
        ("service_date", D),
        ("code_system", S),
        ("procedure_code", S),
        ("service_key", S),
        ("modifier_1", S),
        ("modifier_2", S),
        ("modifier_3", S),
        ("modifier_4", S),
        ("units", INT),
        ("revenue_code", S),
        ("place_of_service", S),
        ("rendering_provider_npi", S),
        ("diagnosis_pointers", S),
        money("billed_amount"),
        money("allowed_amount"),
        money("paid_amount"),
        money("copay"),
        money("coinsurance"),
        money("deductible"),
        money("member_responsibility"),
        money("cob_paid_amount"),
        money("gross_allowed_amount"),
        ("line_status", S),
        ("denial_carc", S),
        ("denial_rarc", S),
    ]
)
DX_SCHEMA = pa.schema(
    [
        ("claim_id", S),
        ("claim_version", INT),
        ("sequence", INT),
        ("code_system", S),
        ("icd10_code", S),
        ("diagnosis_code", S),
        ("poa_indicator", S),
        ("diagnosis_type", S),
        ("service_date", D),
    ]
)
PROC_SCHEMA = pa.schema(
    [
        ("claim_id", S),
        ("claim_version", INT),
        ("sequence", INT),
        ("code_system", S),
        ("icd10pcs_code", S),
        ("procedure_date", D),
    ]
)
AUTH_SCHEMA = pa.schema(
    [
        ("authorization_id", S),
        ("member_id", S),
        ("plan_id", S),
        ("service_key", S),
        ("service_code", S),
        ("requested_date", D),
        ("decision_date", D),
        ("status", S),
        ("valid_from", D),
        ("valid_to", D),
        ("units_authorized", INT),
        ("retroactive", B),
    ]
)


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
        readmit = (
            stay_root.get(d.enc.readmit_of)
            if d.enc.readmit_of and d.facility_type == "inpatient"
            else None
        )
        for v in d.versions:
            cid = root if v["version"] == 1 else f"{root}-{v['version']}"
            _emit_version(
                cb,
                lic,
                d,
                v,
                cid,
                root,
                prev_id,
                auth_by_draft,
                claims,
                lines,
                dxs,
                procs,
                readmit=readmit,
            )
            prev_id = cid
        if _dup(cb, d):
            first = d.versions[0]
            received = first["received"] + timedelta(days=3)
            dup = {
                "version": 1,
                "freq": "1",
                "status": "denied",
                "denial": ("18", "N522"),
                "received": received,
                "adjudicated": received + timedelta(days=5),
                "paid_date": None,
                "kind": "duplicate",
            }
            _emit_version(
                cb,
                lic,
                d,
                dup,
                f"{root}-D1",
                f"{root}-D1",
                None,
                auth_by_draft,
                claims,
                lines,
                dxs,
                procs,
                duplicate_of=root,
            )
    return {
        "medical_claim": _table(CLAIM_SCHEMA, claims),
        "medical_claim_line": _table(LINE_SCHEMA, lines),
        "claim_diagnosis": _table(DX_SCHEMA, dxs),
        "claim_procedure": _table(PROC_SCHEMA, procs),
        "prior_authorization": _table(
            AUTH_SCHEMA,
            [
                {
                    "authorization_id": a.auth_id,
                    "member_id": f"SYN{a.member + 1:09d}",
                    "plan_id": a.plan_id,
                    "service_key": a.service_key,
                    "service_code": a.code,
                    "requested_date": a.requested,
                    "decision_date": a.decided,
                    "status": a.status,
                    "valid_from": a.valid_from,
                    "valid_to": a.valid_to,
                    "units_authorized": a.units,
                    "retroactive": a.retro,
                }
                for a in cb.auths
            ],
        ),
    }


def _dup(cb: ClaimsBuilder, d: Draft) -> bool:
    rng = np.random.default_rng([cb.seed, 71, d.n])
    return (
        bool(rng.random() < cb.cal.get("claim.duplicate_share"))
        and d.versions[0]["status"] != "denied"
    )


def _amounts(d: Draft, v: dict[str, Any]) -> list[tuple[int, int, int, int, int, int, int, int]]:
    """Per line: billed, allowed, paid, copay, coinsurance, deductible, cob, gross allowed (cents).

    ``allowed`` is net of any other payer's payment, so ``allowed = paid + copay + coinsurance +
    deductible`` holds on every line."""
    kind = v["kind"]
    out = []
    for ln in d.lines:
        if v["status"] == "denied" or kind == "duplicate":
            out.append((ln.billed, 0, 0, 0, 0, 0, 0, 0))
            continue
        member = ln.copay + ln.coins + ln.ded
        gross = ln.allowed
        if kind == "adjusted":
            gross = int(round(ln.allowed * v["scale"]))
            paid = max(0, gross - member - ln.cob)
            gross = paid + member + ln.cob
        else:
            paid = ln.paid
        net = paid + member
        row = (ln.billed, net, paid, ln.copay, ln.coins, ln.ded, ln.cob, gross)
        if kind == "void":
            row = tuple(-x for x in row)  # type: ignore[assignment]
        out.append(row)
    return out


def _emit_version(
    cb: ClaimsBuilder,
    lic: LicensedTables,
    d: Draft,
    v: dict[str, Any],
    cid: str,
    root: str,
    prev: str | None,
    auth_by_draft: dict[int, str],
    claims: list[dict[str, Any]],
    lines: list[dict[str, Any]],
    dxs: list[dict[str, Any]],
    procs: list[dict[str, Any]],
    duplicate_of: str | None = None,
    readmit: str | None = None,
) -> None:
    amounts = _amounts(d, v)
    status = v["status"]
    tot = [sum(a[i] for a in amounts) for i in range(8)]
    denial = v.get("denial")
    carc = denial[0] if denial else None
    rarc = denial[1] if denial else None
    enc = d.enc
    inst = d.ctype == "I"
    key = f"{d.facility_type}:{v['freq']}"
    rendering = d.rendering.npi if d.rendering is not None else None
    payer_id, payer_name = PAYER[d.plan.lob]
    attending = rendering if inst and d.rendering is not None else None
    claims.append(
        {
            "claim_id": cid,
            "claim_root_id": root,
            "claim_version": v["version"],
            "original_claim_id": prev,
            "duplicate_of_claim_id": duplicate_of,
            "payer_claim_number": cid,
            "claim_frequency_code": v["freq"],
            "claim_type": d.ctype,
            "claim_status": "denied" if (status == "denied" or duplicate_of) else status,
            "member_id": d.member.member_id,
            "subscriber_id": d.member.subscriber_id,
            "plan_id": d.plan.plan_id,
            "payer_id": payer_id,
            "payer_name": payer_name,
            "line_of_business": d.plan.lob,
            "service_from_date": d.frm,
            "service_to_date": d.to,
            "received_date": v["received"],
            "adjudication_date": v["adjudicated"],
            "paid_date": v["paid_date"],
            "facility_type": d.facility_type,
            "type_of_bill": lic.tob.get(key) if inst else None,
            "place_of_service": d.pos,
            "admission_date": enc.admit if d.facility_type == "inpatient" else None,
            "discharge_date": enc.discharge if d.facility_type == "inpatient" else None,
            "length_of_stay": d.inpatient_days if d.facility_type == "inpatient" else None,
            "admission_type_code": ("1" if d.emergency else "3") if inst else None,
            "patient_status_code": d.status_code,
            "drg_code": d.drg,
            "emergency_flag": d.emergency,
            "readmission_of_claim_root": readmit,
            "billing_npi": d.billing.npi,
            "rendering_npi": rendering if not inst else None,
            "facility_npi": d.billing.npi if inst else None,
            "attending_npi": attending,
            "network_status": "in-network" if d.billing.network else "out-of-network",
            "prior_auth_number": auth_by_draft.get(d.n),
            "encounter_id": enc.eid,
            "total_billed": dollars(tot[0]),
            "total_allowed": dollars(tot[1]),
            "total_paid": dollars(tot[2]),
            "member_copay": dollars(tot[3]),
            "member_coinsurance": dollars(tot[4]),
            "member_deductible": dollars(tot[5]),
            "member_responsibility": dollars(tot[3] + tot[4] + tot[5]),
            "cob_paid_amount": dollars(tot[6]),
            "gross_allowed_amount": dollars(tot[7]),
            "denial_carc": carc,
            "denial_rarc": rarc,
            "denial_description": (CARC[carc].desc if carc else None),
            "module": enc.module,
        }
    )
    for i, (ln, a) in enumerate(zip(d.lines, amounts, strict=True), start=1):
        sys_, code = code_for(ln.svc, lic.cpt)
        mods = list(ln.modifiers) + [None, None, None, None]
        lines.append(
            {
                "claim_id": cid,
                "claim_version": v["version"],
                "line_number": i,
                "service_date": ln.day,
                "code_system": sys_,
                "procedure_code": code,
                "service_key": ln.svc.key,
                "modifier_1": mods[0],
                "modifier_2": mods[1],
                "modifier_3": mods[2],
                "modifier_4": mods[3],
                "units": ln.units,
                "revenue_code": lic.revenue.get(ln.svc.key) if inst else None,
                "place_of_service": d.pos,
                "rendering_provider_npi": ln.rendering.npi if ln.rendering else None,
                "diagnosis_pointers": ",".join(str(p) for p in ln.dx_ptr) if not inst else None,
                "billed_amount": dollars(a[0]),
                "allowed_amount": dollars(a[1]),
                "paid_amount": dollars(a[2]),
                "copay": dollars(a[3]),
                "coinsurance": dollars(a[4]),
                "deductible": dollars(a[5]),
                "member_responsibility": dollars(a[3] + a[4] + a[5]),
                "cob_paid_amount": dollars(a[6]),
                "gross_allowed_amount": dollars(a[7]),
                "line_status": "denied"
                if (status == "denied" or duplicate_of)
                else "reversed"
                if v["kind"] == "void"
                else "paid",
                "denial_carc": carc,
                "denial_rarc": rarc,
            }
        )
    for n, (code, poa) in enumerate(d.dx, start=1):
        dxs.append(
            {
                "claim_id": cid,
                "claim_version": v["version"],
                "sequence": n,
                "code_system": "ICD-10-CM",
                "icd10_code": no_dot(code),
                "diagnosis_code": code,
                "poa_indicator": poa if d.facility_type == "inpatient" else None,
                "diagnosis_type": "principal" if n == 1 else "secondary",
                "service_date": d.frm,
            }
        )
    for n, code in enumerate(d.pcs, start=1):
        procs.append(
            {
                "claim_id": cid,
                "claim_version": v["version"],
                "sequence": n,
                "code_system": "ICD-10-PCS",
                "icd10pcs_code": code,
                "procedure_date": d.frm,
            }
        )


# ---------------------------------------------------------------------------------------------
RX_SCHEMA = pa.schema(
    [
        ("rx_claim_id", S),
        ("member_id", S),
        ("plan_id", S),
        ("line_of_business", S),
        ("group_id", S),
        ("bin_number", S),
        ("pcn", S),
        ("fill_date", D),
        ("written_date", D),
        ("rx_number", S),
        ("refill_number", INT),
        ("transaction_code", S),
        ("claim_status", S),
        ("reject_code", S),
        ("reject_description", S),
        ("ndc", S),
        ("drug_name", S),
        ("brand_name", S),
        ("strength", S),
        ("dose_form", S),
        ("route", S),
        ("therapeutic_class", S),
        ("dea_schedule", S),
        ("brand_generic", S),
        ("quantity", F),
        ("days_supply", INT),
        ("daw_code", S),
        ("prescriber_npi", S),
        ("pharmacy_npi", S),
        ("pharmacy_ncpdp_id", S),
        ("mail_order", B),
        ("pharmacy_type", S),
        ("formulary_tier", INT),
        money("ingredient_cost"),
        money("dispensing_fee"),
        money("patient_pay"),
        money("plan_paid"),
        ("rx_order_id", INT),
        ("early_refill_flag", B),
        ("primary_fill_flag", B),
    ]
)
ORDER_SCHEMA = pa.schema(
    [
        ("rx_order_id", INT),
        ("member_id", S),
        ("written_date", D),
        ("drug_name", S),
        ("strength", S),
        ("quantity", F),
        ("days_supply", INT),
        ("refills_authorized", INT),
        ("daw_code", S),
        ("prescriber_npi", S),
        ("indication", S),
        ("indication_icd10cm", S),
        ("source_encounter_id", INT),
        ("acute_flag", B),
    ]
)
NDC_SCHEMA = pa.schema(
    [
        ("ndc", S),
        ("ndc_formatted", S),
        ("drug_key", S),
        ("drug_name", S),
        ("generic_name", S),
        ("strength", S),
        ("dose_form", S),
        ("route", S),
        ("rxnorm_code", S),
        ("therapeutic_class", S),
        ("brand_generic", S),
        ("dea_schedule", S),
        ("labeler", S),
        ("package_units", F),
        ("marketing_start_date", D),
        ("marketing_end_date", D),
        ("formulary_tier", INT),
        ("source", S),
    ]
)
ADH_SCHEMA = pa.schema(
    [
        ("member_id", S),
        ("measurement_year", INT),
        ("therapeutic_group", S),
        ("first_fill_date", D),
        ("period_days", INT),
        ("days_covered", INT),
        ("pdc", F),
        ("adherent_pdc_80", B),
        ("fills", INT),
        ("gap_days", INT),
        ("max_gap_days", INT),
        ("early_refills", INT),
        ("abandoned_flag", B),
    ]
)
BIN = {"commercial": "990001", "ma": "990002", "medicaid": "990003"}


def rx_tables(rb: RxBuilder, ndc: NdcDirectory | None = None) -> dict[str, pa.Table]:
    rows = []
    for k, f in enumerate(rb.fills, start=1):
        d = f.order.drug
        pharm = rb.dir.info(f.pharmacy)
        sign = -1 if f.status == "reversed" else 1
        rows.append(
            {
                "rx_claim_id": f"RXC{k:010d}",
                "member_id": f.member.member_id,
                "plan_id": f.plan.plan_id,
                "line_of_business": f.plan.lob,
                "group_id": f.member.group_id,
                "bin_number": BIN[f.plan.lob],
                "pcn": "SYNTH",
                "fill_date": f.day,
                "written_date": f.order.written,
                "rx_number": f"{f.order.oid:08d}",
                "refill_number": f.fill_number,
                "transaction_code": f.txn,
                "claim_status": f.status,
                "reject_code": f.reject,
                "reject_description": NCPDP_REJECT[f.reject] if f.reject else None,
                "ndc": f.ndc,
                "drug_name": d.brand_name or d.name,
                "brand_name": d.brand_name,
                "strength": d.strength,
                "dose_form": d.form,
                "route": d.route,
                "therapeutic_class": d.cls,
                "dea_schedule": f"C{d.schedule}" if d.schedule else None,
                "brand_generic": "B" if d.brand else "G",
                "quantity": f.quantity,
                "days_supply": f.days_supply,
                "daw_code": f.order.daw,
                "prescriber_npi": rb.dir.info(f.order.prescriber).npi,
                "pharmacy_npi": pharm.npi,
                "pharmacy_ncpdp_id": f"9{pharm.idx:06d}",
                "mail_order": f.mail,
                "pharmacy_type": "mail" if f.mail else "retail",
                "formulary_tier": f.tier,
                "ingredient_cost": dollars(sign * f.ingredient),
                "dispensing_fee": dollars(sign * f.fee),
                "patient_pay": dollars(sign * f.patient),
                "plan_paid": dollars(sign * f.paid),
                "rx_order_id": f.order.oid,
                "early_refill_flag": f.early,
                "primary_fill_flag": f.primary,
            }
        )
    orders = [
        {
            "rx_order_id": o.oid,
            "member_id": f"SYN{o.member + 1:09d}",
            "written_date": o.written,
            "drug_name": o.drug.brand_name or o.drug.name,
            "strength": o.drug.strength,
            "quantity": o.quantity,
            "days_supply": o.days_supply,
            "refills_authorized": o.refills,
            "daw_code": o.daw,
            "prescriber_npi": rb.dir.info(o.prescriber).npi,
            "indication": o.indication,
            "indication_icd10cm": o.dx,
            "source_encounter_id": o.source,
            "acute_flag": o.acute,
        }
        for o in rb.orders
    ]
    directory = ndc or rb.ndc
    nrows = []
    used = {f.ndc for f in rb.fills}
    for rec in sorted(directory.all_packages(), key=lambda r: r.ndc):
        if rec.ndc not in used:
            continue
        d = DRUGS[rec.drug_key]
        nrows.append(
            {
                "ndc": rec.ndc,
                "ndc_formatted": format_ndc(rec.ndc),
                "drug_key": d.key,
                "drug_name": d.brand_name or d.name,
                "generic_name": d.name,
                "strength": d.strength,
                "dose_form": d.form,
                "route": d.route,
                "rxnorm_code": rec.rxnorm,
                "therapeutic_class": d.cls,
                "brand_generic": "B" if d.brand else "G",
                "dea_schedule": f"C{d.schedule}" if d.schedule else None,
                "labeler": rec.labeler or f"Labeler {rec.ndc[:5]}",
                "package_units": rec.package_units,
                "marketing_start_date": rec.marketing_start,
                "marketing_end_date": None if rec.marketing_end.year > 9000 else rec.marketing_end,
                "formulary_tier": d.tier,
                "source": directory.source,
            }
        )
    adh = [
        {
            "member_id": f"SYN{r['member_idx'] + 1:09d}",
            "measurement_year": r["year"],
            "therapeutic_group": r["therapeutic_group"],
            "first_fill_date": r["first_fill_date"],
            "period_days": r["period_days"],
            "days_covered": r["days_covered"],
            "pdc": round(r["pdc"], 4),
            "adherent_pdc_80": r["pdc"] >= 0.8,
            "fills": r["fills"],
            "gap_days": r["gap_days"],
            "max_gap_days": r["max_gap_days"],
            "early_refills": r["early_refills"],
            "abandoned_flag": r["abandoned"],
        }
        for r in rb.adherence_rows()
    ]
    return {
        "pharmacy_claim": _table(RX_SCHEMA, rows),
        "rx_order": _table(ORDER_SCHEMA, orders),
        "drug_reference": _table(NDC_SCHEMA, nrows),
        "rx_adherence": _table(ADH_SCHEMA, adh),
    }


# ---------------------------------------------------------------------------------------------
RISK_SCHEMA = pa.schema(
    [
        ("member_id", S),
        ("risk_year", INT),
        ("raf_prospective", F),
        ("raf_concurrent", F),
        ("hcc_prospective", S),
        ("hcc_concurrent", S),
        ("age_at_year_start", INT),
        ("model", S),
    ]
)
ACC_SCHEMA = pa.schema(
    [
        ("member_id", S),
        ("scope", S),
        ("plan_year", INT),
        ("plan_id", S),
        money("deductible_limit"),
        money("deductible_met"),
        money("oop_limit"),
        money("oop_met"),
        money("rx_oop_met"),
    ]
)


def risk_table(members: list[Member], cb: ClaimsBuilder, start: date, end: date) -> pa.Table:
    by: dict[tuple[int, int], set[str]] = defaultdict(set)
    for d in cb.drafts:
        if not d.final_paid:
            continue
        for code, _ in d.dx:
            by[(d.member.idx, d.frm.year)].add(code)
    rows = []
    for m in members:
        years = sorted(
            {
                y
                for s in m.spans
                for y in range(max(s.start, start).year, (s.end or end).year + 1)
                if start.year <= y <= end.year
            }
        )
        for y in years:
            cur = hccs_for(by.get((m.idx, y), ()))
            prior = hccs_for(by.get((m.idx, y - 1), ())) if y > start.year else None
            age = m.age(date(y, 1, 1))
            rows.append(
                {
                    "member_id": m.member_id,
                    "risk_year": y,
                    "raf_prospective": raf(age, m.sex, prior) if prior is not None else None,
                    "raf_concurrent": raf(age, m.sex, cur),
                    "hcc_prospective": ",".join(map(str, prior)) if prior is not None else None,
                    "hcc_concurrent": ",".join(map(str, cur)),
                    "age_at_year_start": age,
                    "model": "CMS-HCC V28 (seed weights, illustrative)",
                }
            )
    return _table(RISK_SCHEMA, rows)


def accumulator_table(
    cb: ClaimsBuilder, rb: RxBuilder, members: list[Member], plans: dict[str, Any]
) -> pa.Table:
    rows = []
    fam_rows = []
    first_of: dict[int, Member] = {}
    for m in members:
        first_of.setdefault(m.household, m)
    for (idx, year, plan_id), a in sorted(cb.ind.items()):
        p = plans[plan_id]
        rows.append(
            {
                "member_id": members[idx].member_id,
                "scope": "individual",
                "plan_year": year,
                "plan_id": plan_id,
                "deductible_limit": p.deductible,
                "deductible_met": dollars(a.ded),
                "oop_limit": p.oop_max,
                "oop_met": dollars(a.oop),
                "rx_oop_met": dollars(rb.rx_oop.get((idx, year, plan_id), 0)),
            }
        )
    for (hh, year, _lob, plan_id), a in sorted(cb.fam.items()):
        p = plans[plan_id]
        sub = first_of.get(hh)
        fam_rows.append(
            {
                "member_id": sub.member_id if sub else None,
                "scope": "family",
                "plan_year": year,
                "plan_id": plan_id,
                "deductible_limit": p.deductible * p.family_multiple,
                "deductible_met": dollars(a.ded),
                "oop_limit": p.oop_max * p.family_multiple,
                "oop_met": dollars(a.oop),
                "rx_oop_met": None,
            }
        )
    return _table(ACC_SCHEMA, rows + fam_rows)
