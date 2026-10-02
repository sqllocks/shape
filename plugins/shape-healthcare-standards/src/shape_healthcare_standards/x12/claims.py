"""837P (005010X222A1) and 837I (005010X223A2) health care claims."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from ..common import TableSet, d8, money, money_text, number_text
from ..contract import ContractError
from .core import Delimiters, EnvelopeOptions, Segments, clean, interchange, need, transaction
from .model import (
    Row,
    eligibility_for,
    filing_code,
    member_row,
    payer_for,
    provider_name_parts,
    provider_row,
    subscriber_of,
)

VERSION_837P = "005010X222A1"
VERSION_837I = "005010X223A2"
# 005010 requires a 9-digit ZIP for the billing provider; a synthetic ZIP has no real +4.
ZIP4_PLACEHOLDER = "9999"


@dataclass(frozen=True, slots=True)
class ClaimOptions:
    """Values the claim tables do not carry. Defaults are clearly synthetic."""

    submitter_name: str = "SYNTHETIC SUBMITTER"
    submitter_id: str = "SYNTHSUB01"
    receiver_name: str = "SYNTHETIC RECEIVER"
    receiver_id: str = "SYNTHRECEIVER"
    contact_name: str = "SYNTHETIC CONTACT"
    contact_phone: str = "5555550100"
    default_payer_name: str = "SYNTHETIC PAYER"
    default_payer_id: str = "SYNTHPAYER"
    max_claims_per_file: int = 5000
    strict_totals: bool = True
    admission_hour: str = "0000"


def _address(seg: Segments, row: Row, where: str, *, required: bool, zip9: bool = False) -> None:
    street, city = row.get("address_line1"), row.get("city")
    state, zip_ = row.get("state"), row.get("zip")
    if not (street and city and state and zip_):
        if required:
            raise ContractError(f"{where}: street address, city, state and zip are required")
        return
    lines = [clean(street, limit=55)]
    if row.get("address_line2"):
        lines.append(clean(row["address_line2"], limit=55))
    seg.add("N3", *lines)
    z = "".join(ch for ch in str(zip_) if ch.isdigit())
    if zip9 and len(z) == 5:
        z += ZIP4_PLACEHOLDER
    seg.add("N4", clean(city, limit=30), clean(state, limit=2), z)


def _nm1(
    seg: Segments,
    code: str,
    qual: str,
    last: str,
    first: str = "",
    middle: str = "",
    id_qual: str = "",
    ident: str = "",
) -> None:
    seg.add("NM1", code, qual, last, first, middle, "", "", id_qual, ident)


def _group_by_billing(claims: Sequence[Row]) -> dict[str, list[Row]]:
    groups: dict[str, list[Row]] = {}
    for c in claims:
        groups.setdefault(c["billing_npi"], []).append(c)
    return dict(sorted(groups.items()))


def _diagnoses(ts: TableSet, claim_id: str) -> list[Row]:
    return sorted(
        ts.by("claim_diagnosis", "claim_id").get(claim_id, []), key=lambda r: r["sequence"]
    )


def _lines(ts: TableSet, claim_id: str) -> list[Row]:
    return sorted(
        ts.by("medical_claim_line", "claim_id").get(claim_id, []), key=lambda r: r["line_number"]
    )


def _check_totals(claim: Row, lines: Sequence[Row], opts: ClaimOptions) -> None:
    total = money(claim["total_billed"])
    got = sum((money(ln["billed_amount"]) for ln in lines), Decimal("0.00"))
    if opts.strict_totals and got != total:
        raise ContractError(
            f"claim {claim['claim_id']}: total_billed {total} != sum of line billed_amount {got}"
        )


def _frequency(claim: Row) -> str:
    return str(claim.get("claim_frequency_code") or "1")


def _reference_for_replacement(seg: Segments, claim: Row) -> None:
    if _frequency(claim) in ("7", "8"):
        ref = claim.get("original_claim_id") or claim.get("payer_claim_number")
        seg.add(
            "REF",
            "F8",
            clean(need(ref, "original claim reference", f"claim {claim['claim_id']}"), limit=50),
        )


def _hi(seg: Segments, items: list[str]) -> None:
    """Diagnosis or procedure composites into ``HI`` segments of up to 12 each."""
    for i in range(0, len(items), 12):
        seg.add("HI", *items[i : i + 12])


def _pro_claim(seg: Segments, ts: TableSet, claim: Row, opts: ClaimOptions, billing: Row) -> None:
    cid = str(claim["claim_id"])
    where = f"claim {cid}"
    lines = _lines(ts, cid)
    if not lines:
        raise ContractError(f"{where}: no service lines in medical_claim_line")
    diags = _diagnoses(ts, cid)
    if not diags:
        raise ContractError(f"{where}: no diagnoses in claim_diagnosis")
    _check_totals(claim, lines, opts)
    pos = need(
        claim.get("place_of_service") or lines[0].get("place_of_service"), "place of service", where
    )
    seg.add(
        "CLM",
        clean(cid, upper=False, limit=38),
        money_text(claim["total_billed"]),
        "",
        "",
        seg.comp(pos, "B", _frequency(claim)),
        "Y",
        "A",
        "Y",
        "Y",
    )
    if claim.get("prior_auth_number"):
        seg.add("REF", "G1", clean(claim["prior_auth_number"], limit=50))
    _reference_for_replacement(seg, claim)
    items = []
    for i, d in enumerate(diags):
        items.append(seg.comp("ABK" if i == 0 else "ABF", clean(d["icd10_code"])))
    _hi(seg, items)
    rendering = claim.get("rendering_npi")
    if rendering and rendering != claim["billing_npi"]:
        r = provider_row(ts, rendering, where)
        q, last, first = provider_name_parts(r)
        _nm1(seg, "82", q, last, first, "", "XX", rendering)
        if r.get("taxonomy_code"):
            seg.add("PRV", "PE", "PXC", clean(r["taxonomy_code"]))
    facility = claim.get("facility_npi")
    if facility and facility != claim["billing_npi"]:
        f = provider_row(ts, facility, where)
        q, last, first = provider_name_parts(f)
        _nm1(seg, "77", q, last, first, "", "XX", facility)
        _address(seg, f, f"{where} service facility", required=True)
    for n, ln in enumerate(lines, start=1):
        _pro_line(seg, ln, n, where)


def _pro_line(seg: Segments, ln: Row, n: int, where: str) -> None:
    code = need(ln.get("procedure_code"), "procedure code", f"{where} line {ln['line_number']}")
    mods = [clean(ln[f"modifier_{i}"]) for i in range(1, 5) if ln.get(f"modifier_{i}")]
    pointers = ":".join(
        p.strip() for p in str(ln.get("diagnosis_pointers") or "1").split(",") if p.strip()
    )
    seg.add("LX", n)
    seg.add(
        "SV1",
        seg.comp("HC", clean(code), *mods),
        money_text(ln["billed_amount"]),
        "UN",
        number_text(ln.get("units") if ln.get("units") is not None else 1.0),
        "",
        "",
        pointers,
    )
    seg.add("DTP", "472", "D8", d8(ln["service_date"]))
    seg.add("REF", "6R", str(ln["line_number"]))


def _inst_claim(seg: Segments, ts: TableSet, claim: Row, opts: ClaimOptions, billing: Row) -> None:
    cid = str(claim["claim_id"])
    where = f"claim {cid}"
    lines = _lines(ts, cid)
    if not lines:
        raise ContractError(f"{where}: no service lines in medical_claim_line")
    diags = _diagnoses(ts, cid)
    if not diags:
        raise ContractError(f"{where}: no diagnoses in claim_diagnosis")
    _check_totals(claim, lines, opts)
    tob = need(claim.get("type_of_bill"), "type of bill", where)
    if len(tob) != 3 or not tob.isdigit():
        raise ContractError(f"{where}: type_of_bill {tob!r} must be 3 digits")
    freq = tob[2]
    seg.add(
        "CLM",
        clean(cid, upper=False, limit=38),
        money_text(claim["total_billed"]),
        "",
        "",
        seg.comp(tob[:2], "A", freq),
        "",
        "A",
        "Y",
        "Y",
    )
    frm, to = claim["service_from_date"], claim["service_to_date"]
    seg.add("DTP", "434", "RD8", f"{d8(frm)}-{d8(to)}")
    if claim.get("discharge_date"):
        seg.add("DTP", "096", "TM", opts.admission_hour)
    if claim.get("admission_date"):
        seg.add("DTP", "435", "DT", f"{d8(claim['admission_date'])}{opts.admission_hour}")
    seg.add(
        "CL1",
        clean(claim.get("admission_type_code")),
        "",
        clean(claim.get("patient_status_code")),
    )
    if claim.get("prior_auth_number"):
        seg.add("REF", "G1", clean(claim["prior_auth_number"], limit=50))
    if freq in ("7", "8"):
        ref = claim.get("original_claim_id") or claim.get("payer_claim_number")
        seg.add("REF", "F8", clean(need(ref, "original claim reference", where), limit=50))
    _inst_diagnoses(seg, diags, where)
    if claim.get("drg_code"):
        seg.add("HI", seg.comp("DR", clean(claim["drg_code"])))
    procs = sorted(ts.by("claim_procedure", "claim_id").get(cid, []), key=lambda r: r["sequence"])
    items = []
    for i, p in enumerate(procs):
        when = p.get("procedure_date") or claim.get("admission_date") or claim["service_from_date"]
        items.append(
            seg.comp("BBR" if i == 0 else "BBQ", clean(p["icd10pcs_code"]), "D8", d8(when))
        )
    _hi(seg, items)
    attending = claim.get("attending_npi")
    if attending:
        a = provider_row(ts, attending, where)
        q, last, first = provider_name_parts(a)
        _nm1(seg, "71", q, last, first, "", "XX", attending)
        if a.get("taxonomy_code"):
            seg.add("PRV", "AT", "PXC", clean(a["taxonomy_code"]))
    facility = claim.get("facility_npi")
    if facility and facility != claim["billing_npi"]:
        f = provider_row(ts, facility, where)
        q, last, first = provider_name_parts(f)
        _nm1(seg, "77", q, last, first, "", "XX", facility)
        _address(seg, f, f"{where} service facility", required=True)
    for n, ln in enumerate(lines, start=1):
        _inst_line(seg, ln, n, where)


def _inst_diagnoses(seg: Segments, diags: Sequence[Row], where: str) -> None:
    def comp(qual: str, d: Row) -> str:
        poa = clean(d.get("poa_indicator"))
        code = clean(d["icd10_code"])
        if poa:
            return seg.comp(qual, code, "", "", "", "", "", "", poa)
        return seg.comp(qual, code)

    principal = next((d for d in diags if d.get("diagnosis_type") == "principal"), diags[0])
    seg.add("HI", comp("ABK", principal))
    admitting = [d for d in diags if d.get("diagnosis_type") == "admitting" and d is not principal]
    if admitting:
        seg.add("HI", comp("ABJ", admitting[0]))
    others = [d for d in diags if d is not principal and not (admitting and d is admitting[0])]
    items = [comp("ABF", d) for d in others]
    _hi(seg, items)


def _inst_line(seg: Segments, ln: Row, n: int, where: str) -> None:
    rev = need(ln.get("revenue_code"), "revenue code", f"{where} line {ln['line_number']}")
    seg.add("LX", n)
    mods = [clean(ln[f"modifier_{i}"]) for i in range(1, 5) if ln.get(f"modifier_{i}")]
    proc = seg.comp("HC", clean(ln["procedure_code"]), *mods) if ln.get("procedure_code") else ""
    seg.add(
        "SV2",
        clean(rev),
        proc,
        money_text(ln["billed_amount"]),
        "UN",
        number_text(ln.get("units") if ln.get("units") is not None else 1.0),
    )
    seg.add("DTP", "472", "D8", d8(ln["service_date"]))
    seg.add("REF", "6R", str(ln["line_number"]))


def _patient_loops(
    seg: Segments,
    ts: TableSet,
    claims: Sequence[Row],
    opts: ClaimOptions,
    kind: str,
    hl: list[int],
    billing_hl: int,
) -> None:
    """2000B subscriber loops (and 2000C patient loops) for one billing provider's claims."""
    by_sub: dict[str, list[Row]] = {}
    where_of: dict[str, Row] = {}
    for c in claims:
        w = f"claim {c['claim_id']}"
        patient = member_row(ts, c["member_id"], w)
        sub = subscriber_of(ts, patient, w)
        by_sub.setdefault(sub["member_id"], []).append(c)
        where_of[sub["member_id"]] = sub
    for sub_id in sorted(by_sub):
        sub = where_of[sub_id]
        sub_claims = by_sub[sub_id]
        own = [c for c in sub_claims if c["member_id"] == sub_id]
        dependents: dict[str, list[Row]] = {}
        for c in sub_claims:
            if c["member_id"] != sub_id:
                dependents.setdefault(c["member_id"], []).append(c)
        hl[0] += 1
        sub_hl = hl[0]
        seg.add("HL", sub_hl, billing_hl, "22", 1 if dependents else 0)
        first_claim = sub_claims[0]
        elig = eligibility_for(ts, first_claim["member_id"], first_claim["service_from_date"])
        sbr = ["P", "18" if own else "", "", "", "", "", "", "", filing_code(elig)]
        if elig and elig.get("group_id"):
            sbr[2] = clean(elig["group_id"], limit=50)
        if elig and elig.get("group_name"):
            sbr[3] = clean(elig["group_name"], limit=60)
        seg.add("SBR", *sbr)
        w = f"subscriber {sub_id}"
        _nm1(
            seg,
            "IL",
            "1",
            clean(sub["last_name"]),
            clean(sub["first_name"]),
            clean(sub.get("middle_name"))[:1],
            "MI",
            clean(need(sub["subscriber_id"], "subscriber id", w), limit=80),
        )
        _address(seg, sub, w, required=True)
        seg.add("DMG", "D8", d8(sub["birth_date"]), clean(sub["sex"] or "U"))
        payer = payer_for(first_claim, elig, opts.default_payer_name, opts.default_payer_id)
        _nm1(seg, "PR", "2", payer.name, "", "", "PI", payer.ident)
        for c in own:
            _claim(seg, ts, c, opts, kind)
        for dep_id in sorted(dependents):
            dep = member_row(ts, dep_id, f"member {dep_id}")
            hl[0] += 1
            seg.add("HL", hl[0], sub_hl, "23", 0)
            seg.add("PAT", clean(dep["relationship_code"]))
            _nm1(
                seg,
                "QC",
                "1",
                clean(dep["last_name"]),
                clean(dep["first_name"]),
                clean(dep.get("middle_name"))[:1],
            )
            _address(seg, dep, f"member {dep_id}", required=False)
            seg.add("DMG", "D8", d8(dep["birth_date"]), clean(dep["sex"] or "U"))
            for c in dependents[dep_id]:
                _claim(seg, ts, c, opts, kind)


def _claim(seg: Segments, ts: TableSet, claim: Row, opts: ClaimOptions, kind: str) -> None:
    billing = provider_row(ts, claim["billing_npi"], f"claim {claim['claim_id']}")
    if kind == "P":
        _pro_claim(seg, ts, claim, opts, billing)
    else:
        _inst_claim(seg, ts, claim, opts, billing)


def _billing_loop(seg: Segments, ts: TableSet, npi: str, kind: str) -> None:
    where = f"billing provider {npi}"
    b = provider_row(ts, npi, where)
    q, last, first = provider_name_parts(b)
    if kind == "P" and b.get("taxonomy_code"):
        seg.add("PRV", "BI", "PXC", clean(b["taxonomy_code"]))
    _nm1(seg, "85", q, last, first, "", "XX", npi)
    _address(seg, b, where, required=True, zip9=True)
    seg.add("REF", "EI", clean(need(b.get("tax_id"), "tax id", where), limit=20))


def build_claims(
    ts: TableSet,
    kind: str,
    opts: ClaimOptions,
    env: EnvelopeOptions,
    when: dt.datetime | None = None,
) -> list[str]:
    """One interchange (as text) per ``max_claims_per_file`` claims of the given kind."""
    if kind not in ("P", "I"):
        raise ValueError("kind must be 'P' or 'I'")
    claims = [c for c in ts.rows("medical_claim") if c["claim_type"] == kind]
    if not claims:
        return []
    claims.sort(key=lambda c: (c["billing_npi"], str(c["claim_id"])))
    chunks = [
        claims[i : i + opts.max_claims_per_file]
        for i in range(0, len(claims), opts.max_claims_per_file)
    ]
    version = VERSION_837P if kind == "P" else VERSION_837I
    files: list[str] = []
    for n, chunk in enumerate(chunks):
        e = _env_for(env, n)
        files.append(_one_interchange(ts, chunk, kind, version, opts, e))
    return files


def _env_for(env: EnvelopeOptions, n: int) -> EnvelopeOptions:
    return replace(
        env,
        interchange_control=env.interchange_control + n,
        group_control=env.group_control + n,
    )


def _one_interchange(
    ts: TableSet,
    claims: Sequence[Row],
    kind: str,
    version: str,
    opts: ClaimOptions,
    env: EnvelopeOptions,
) -> str:
    d: Delimiters = env.delimiters
    seg = Segments(d)
    created = env.created
    seg.add(
        "BHT",
        "0019",
        "00",
        f"{kind}{env.interchange_control:09d}",
        created.strftime("%Y%m%d"),
        created.strftime("%H%M"),
        "CH",
    )
    seg.add(
        "NM1",
        "41",
        "2",
        clean(opts.submitter_name, limit=60),
        "",
        "",
        "",
        "",
        "46",
        clean(opts.submitter_id, limit=80),
    )
    seg.add(
        "PER", "IC", clean(opts.contact_name, limit=60), "TE", clean(opts.contact_phone, limit=20)
    )
    seg.add(
        "NM1",
        "40",
        "2",
        clean(opts.receiver_name, limit=60),
        "",
        "",
        "",
        "",
        "46",
        clean(opts.receiver_id, limit=80),
    )
    hl = [0]
    for npi, group in _group_by_billing(claims).items():
        hl[0] += 1
        billing_hl = hl[0]
        seg.add("HL", billing_hl, "", "20", 1)
        _billing_loop(seg, ts, npi, kind)
        _patient_loops(seg, ts, group, opts, kind, hl, billing_hl)
    block = transaction(
        seg.items,
        set_id="837",
        version=version,
        control=env.first_set_control,
        delimiters=d,
    )
    return interchange([block], functional_id="HC", version=version, options=env)
