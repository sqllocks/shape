"""835 health care claim payment/advice (005010X221A1).

Each payee (billing provider) gets one transaction set. Every adjustment sits on its service
line (``SVC`` / ``CAS``), so each line and each claim balances: billed - paid = the sum of
its adjustments. Patient responsibility is reported as group ``PR`` (1 deductible,
2 coinsurance, 3 copay), the contractual write-down (billed - allowed) as ``CO`` 45, and a
denied line as ``CO`` with its CARC and the RARC in ``LQ*HE``. A reversal reports every
amount negated.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from ..common import TableSet, d8, money, money_text, number_text
from ..contract import ContractError
from .core import EnvelopeOptions, Segments, clean, interchange, need, transaction
from .model import (
    Row,
    eligibility_for,
    member_row,
    payer_for,
    provider_name_parts,
    provider_row,
    remit_filing_code,
)

VERSION_835 = "005010X221A1"
CLAIM_STATUS_CODE = {"paid": "1", "adjusted": "1", "denied": "4", "reversed": "22"}
ZERO = Decimal("0.00")


@dataclass(frozen=True, slots=True)
class RemitOptions:
    """Values the claim tables do not carry. Defaults are clearly synthetic."""

    payer_name: str = "SYNTHETIC PAYER"
    payer_id: str = "SYNTHPAYER"
    payer_tax_id: str = "900000099"
    payer_street: str = "1 SYNTHETIC PLAZA"
    payer_city: str = "SPRINGFIELD"
    payer_state: str = "IL"
    payer_zip: str = "62701"
    contact_phone: str = "5555550100"
    payer_routing: str = "000000000"
    payer_account: str = "000000001"
    payee_routing: str = "000000000"
    payee_account: str = "000000002"
    strict_balance: bool = True


@dataclass(slots=True)
class _Adj:
    group: str
    reason: str
    amount: Decimal


def _line_amounts(ln: Row) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal, Decimal]:
    billed = money(ln["billed_amount"])
    paid = money(ln.get("paid_amount"))
    copay, coins, ded = (
        money(ln.get("copay")),
        money(ln.get("coinsurance")),
        money(ln.get("deductible")),
    )
    allowed = (
        money(ln["allowed_amount"])
        if ln.get("allowed_amount") is not None
        else paid + copay + coins + ded
    )
    return billed, allowed, paid, copay, coins, ded


def _adjustments(
    billed: Decimal,
    allowed: Decimal,
    paid: Decimal,
    copay: Decimal,
    coins: Decimal,
    ded: Decimal,
    carc: str | None,
    status: str,
    where: str,
    strict: bool,
) -> list[_Adj]:
    adjs: list[_Adj] = []
    if status == "denied" or (paid == ZERO and allowed == ZERO and carc):
        reason = need(carc, "denial CARC", where)
        return [_Adj("CO", clean(reason), billed)]
    if allowed != paid + copay + coins + ded:
        if strict:
            raise ContractError(
                f"{where}: allowed {allowed} != paid {paid} + member responsibility "
                f"{copay + coins + ded}"
            )
    if ded:
        adjs.append(_Adj("PR", "1", ded))
    if coins:
        adjs.append(_Adj("PR", "2", coins))
    if copay:
        adjs.append(_Adj("PR", "3", copay))
    if billed != allowed:
        adjs.append(_Adj("CO", "45", billed - allowed))
    return adjs


def _cas(seg: Segments, adjs: Sequence[_Adj], sign: int) -> None:
    by_group: dict[str, list[_Adj]] = {}
    for a in adjs:
        by_group.setdefault(a.group, []).append(a)
    for group in sorted(by_group):
        items = by_group[group]
        for i in range(0, len(items), 6):
            elems: list[str] = [group]
            for a in items[i : i + 6]:
                elems += [a.reason, money_text(a.amount * sign), ""]
            seg.add("CAS", *elems)


@dataclass(slots=True)
class _ClaimOut:
    claim: Row
    status: str
    sign: int
    lines: list[Row]
    paid: Decimal
    patient: Decimal
    billed: Decimal


def _claim_out(ts: TableSet, claim: Row, opts: RemitOptions) -> _ClaimOut:
    cid = str(claim["claim_id"])
    status = str(claim.get("claim_status") or "").lower()
    lines = sorted(
        ts.by("medical_claim_line", "claim_id").get(cid, []), key=lambda r: r["line_number"]
    )
    billed = money(claim["total_billed"])
    paid = money(claim.get("total_paid"))
    patient = (
        money(claim.get("member_copay"))
        + money(claim.get("member_coinsurance"))
        + money(claim.get("member_deductible"))
    )
    if lines:
        lb = sum((money(ln["billed_amount"]) for ln in lines), ZERO)
        lp = sum((money(ln.get("paid_amount")) for ln in lines), ZERO)
        if opts.strict_balance and (lb != billed or lp != paid):
            raise ContractError(
                f"claim {cid}: header billed/paid {billed}/{paid} != line sums {lb}/{lp}"
            )
    return _ClaimOut(claim, status, -1 if status == "reversed" else 1, lines, paid, patient, billed)


def _claim_segments(
    seg: Segments, ts: TableSet, out: _ClaimOut, opts: RemitOptions, default: RemitOptions
) -> None:
    claim = out.claim
    cid = str(claim["claim_id"])
    where = f"claim {cid}"
    sign = out.sign
    elig = eligibility_for(ts, claim["member_id"], claim["service_from_date"])
    inst = str(claim["claim_type"]) == "I"
    facility = (
        str(claim.get("type_of_bill") or "")[:2]
        if inst
        else str(claim.get("place_of_service") or "")
    )
    freq = str(claim.get("claim_frequency_code") or "1")
    if inst and claim.get("type_of_bill"):
        freq = str(claim["type_of_bill"])[2:3] or freq
    seg.add(
        "CLP",
        clean(cid, upper=False, limit=38),
        CLAIM_STATUS_CODE[out.status],
        money_text(out.billed * sign),
        money_text(out.paid * sign),
        money_text(out.patient * sign) if out.patient else "",
        remit_filing_code(elig),
        clean(claim.get("payer_claim_number") or cid, limit=50),
        facility,
        freq,
    )
    member = member_row(ts, claim["member_id"], where)
    seg.add(
        "NM1",
        "QC",
        "1",
        clean(member["last_name"]),
        clean(member["first_name"]),
        clean(member.get("middle_name"))[:1],
        "",
        "",
        "MI",
        clean(member["member_id"], limit=80),
    )
    if claim.get("rendering_npi"):
        r = provider_row(ts, claim["rendering_npi"], where)
        q, last, first = provider_name_parts(r)
        seg.add("NM1", "82", q, last, first, "", "", "", "XX", claim["rendering_npi"])
    if claim.get("received_date"):
        seg.add("DTM", "050", d8(claim["received_date"]))
    seg.add("DTM", "232", d8(claim["service_from_date"]))
    seg.add("DTM", "233", d8(claim["service_to_date"]))
    if claim.get("total_allowed") is not None and out.status != "denied":
        seg.add("AMT", "AU", money_text(money(claim["total_allowed"]) * sign))
    if not out.lines:
        # No service lines: the adjustments go on the claim.
        allowed = (
            money(claim["total_allowed"])
            if claim.get("total_allowed") is not None
            else out.paid + out.patient
        )
        adjs = _adjustments(
            out.billed,
            allowed,
            out.paid,
            money(claim.get("member_copay")),
            money(claim.get("member_coinsurance")),
            money(claim.get("member_deductible")),
            None,
            out.status,
            where,
            opts.strict_balance,
        )
        _cas(seg, adjs, sign)
        return
    for ln in out.lines:
        lwhere = f"{where} line {ln['line_number']}"
        billed, allowed, paid, copay, coins, ded = _line_amounts(ln)
        lstatus = "denied" if str(ln.get("line_status") or "").lower() == "denied" else out.status
        adjs = _adjustments(
            billed,
            allowed,
            paid,
            copay,
            coins,
            ded,
            ln.get("denial_carc"),
            lstatus,
            lwhere,
            opts.strict_balance,
        )
        units = number_text(ln.get("units") if ln.get("units") is not None else 1.0)
        rev = clean(ln.get("revenue_code"))
        if ln.get("procedure_code"):
            mods = [clean(ln[f"modifier_{i}"]) for i in range(1, 5) if ln.get(f"modifier_{i}")]
            code = seg.comp("HC", clean(ln["procedure_code"]), *mods)
        else:
            code = seg.comp("NU", rev)
        seg.add(
            "SVC",
            code,
            money_text(billed * sign),
            money_text(paid * sign),
            rev if inst else "",
            units,
        )
        seg.add("DTM", "472", d8(ln["service_date"]))
        _cas(seg, adjs, sign)
        seg.add("REF", "6R", str(ln["line_number"]))
        seg.add("AMT", "B6", money_text(allowed * sign))
        if ln.get("denial_rarc"):
            seg.add("LQ", "HE", clean(ln["denial_rarc"]))


def _payee_set(
    ts: TableSet,
    npi: str,
    claims: Sequence[Row],
    opts: RemitOptions,
    env: EnvelopeOptions,
    control: int,
) -> list[str]:
    seg = Segments(env.delimiters)
    outs = [_claim_out(ts, c, opts) for c in claims]
    total = sum((o.paid * o.sign for o in outs), ZERO)
    when = max(
        (c.get("adjudication_date") or c["service_to_date"] for c in claims),
        default=env.created.date(),
    )
    if not isinstance(when, dt.date):  # pragma: no cover
        when = env.created.date()
    payee = provider_row(ts, npi, f"payee {npi}")
    plb: Decimal = ZERO
    bpr_total = total
    if total < ZERO:
        plb, bpr_total = total, ZERO
    if bpr_total == ZERO:
        seg.add("BPR", "I", "0", "C", "NON", *[""] * 11, d8(when))
    else:
        seg.add(
            "BPR",
            "C",
            money_text(bpr_total),
            "C",
            "ACH",
            "CCP",
            "01",
            opts.payer_routing,
            "DA",
            opts.payer_account,
            "1" + opts.payer_tax_id,
            "",
            "01",
            opts.payee_routing,
            "DA",
            opts.payee_account,
            d8(when),
        )
    seg.add("TRN", "1", f"{control:010d}", "1" + opts.payer_tax_id)
    seg.add("DTM", "405", d8(when))
    default_party = payer_for({}, None, opts.payer_name, opts.payer_id)
    first = claims[0]
    elig = eligibility_for(ts, first["member_id"], first["service_from_date"])
    party = payer_for(first, elig, default_party.name, default_party.ident)
    seg.add("N1", "PR", party.name, "XV", party.ident)
    seg.add("N3", clean(opts.payer_street, limit=55))
    seg.add(
        "N4", clean(opts.payer_city, limit=30), clean(opts.payer_state, limit=2), opts.payer_zip
    )
    seg.add("REF", "2U", party.ident)
    seg.add("PER", "CX", "SYNTHETIC CONTACT", "TE", opts.contact_phone)
    seg.add("PER", "BL", "SYNTHETIC CONTACT", "TE", opts.contact_phone)
    q, last, firstname = provider_name_parts(payee)
    name = last if q == "2" else f"{firstname} {last}".strip()
    seg.add("N1", "PE", name, "XX", npi)
    if payee.get("tax_id"):
        seg.add("REF", "TJ", clean(payee["tax_id"], limit=20))
    seg.add("LX", 1)
    for o in outs:
        _claim_segments(seg, ts, o, opts, opts)
    if plb != ZERO:
        seg.add("PLB", npi, d8(when), seg.comp("FB", f"{control:010d}"), money_text(plb))
    return transaction(
        seg.items,
        set_id="835",
        version=None,
        control=control,
        delimiters=env.delimiters,
    )


def build_remittance(ts: TableSet, opts: RemitOptions, env: EnvelopeOptions) -> list[str]:
    """One interchange holding one transaction set per payee."""
    claims = [
        c
        for c in ts.rows("medical_claim")
        if str(c.get("claim_status") or "").lower() in CLAIM_STATUS_CODE
    ]
    if not claims:
        return []
    groups: dict[str, list[Row]] = {}
    for c in sorted(claims, key=lambda c: (c["billing_npi"], str(c["claim_id"]))):
        groups.setdefault(c["billing_npi"], []).append(c)
    sets: list[list[str]] = []
    control = env.first_set_control
    for npi in sorted(groups):
        sets.append(_payee_set(ts, npi, groups[npi], opts, env, control))
        control += 1
    return [interchange(sets, functional_id="HP", version=VERSION_835, options=env)]
