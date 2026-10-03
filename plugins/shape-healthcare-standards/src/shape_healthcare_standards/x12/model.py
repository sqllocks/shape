"""Lookups over the contract tables that the X12 writers share."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from ..common import TableSet
from ..contract import ContractError
from .core import clean, need

Row = dict[str, Any]

# Eligibility plan type -> X12 claim filing indicator code (SBR09 / CLP06).
FILING_CODE = {
    "COMMERCIAL": "CI",
    "ACA": "CI",
    "MEDICARE_ADVANTAGE": "16",
    "MEDICAID": "MC",
}
DEFAULT_FILING = "CI"
# The 835 CLP06 code list has no commercial-insurance code; ZZ is "mutually defined".
REMIT_FILING_CODE = {"MEDICARE_ADVANTAGE": "16", "MEDICAID": "MC"}
DEFAULT_REMIT_FILING = "ZZ"


@dataclass(frozen=True, slots=True)
class Party:
    """A payer, as written in an ``NM1*PR`` / ``N1*PR`` segment."""

    name: str
    ident: str


def provider_row(ts: TableSet, npi: str | None, where: str) -> Row:
    rows = ts.index("provider", "npi")
    key = need(npi, "provider NPI", where)
    if key not in rows:
        raise ContractError(f"{where}: provider {key!r} is not in the provider table")
    return rows[key]


def provider_name_parts(p: Row) -> tuple[str, str, str]:
    """``(entity qualifier, last/org name, first name)`` for an NM1 segment."""
    if str(p.get("entity_type") or "") == "1":
        return "1", clean(p.get("last_name")), clean(p.get("first_name"))
    return "2", clean(p.get("org_name") or p.get("last_name")), ""


def member_row(ts: TableSet, member_id: str, where: str) -> Row:
    members = ts.index("member", "member_id")
    if member_id not in members:
        raise ContractError(f"{where}: member {member_id!r} is not in the member table")
    return members[member_id]


def subscriber_of(ts: TableSet, member: Row, where: str) -> Row:
    """The subscriber's member row for a member (the member itself when the subscriber)."""
    if str(member["relationship_code"]) == "18":
        return member
    sid = member["subscriber_id"]
    for cand in ts.by("member", "subscriber_id").get(sid, []):
        if str(cand["relationship_code"]) == "18":
            return cand
    raise ContractError(f"{where}: subscriber {sid!r} of member {member['member_id']!r} not found")


def eligibility_for(ts: TableSet, member_id: str, on: dt.date | None) -> Row | None:
    """The member's span covering ``on``, else the latest span, else ``None``."""
    spans = ts.by("eligibility", "member_id").get(member_id, [])
    if not spans:
        return None
    if on is not None:
        for s in spans:
            end = s["coverage_end"]
            if s["coverage_start"] <= on and (end is None or on <= end):
                return s
    return max(spans, key=lambda s: s["coverage_start"])


def payer_for(claim: Row, elig: Row | None, default_name: str, default_id: str) -> Party:
    name = claim.get("payer_name") or (elig or {}).get("payer_name") or default_name
    ident = claim.get("payer_id") or (elig or {}).get("payer_id") or default_id
    return Party(clean(name, limit=60), clean(ident, limit=80))


def filing_code(elig: Row | None) -> str:
    return FILING_CODE.get(str((elig or {}).get("plan_type") or "").upper(), DEFAULT_FILING)


def remit_filing_code(elig: Row | None) -> str:
    return REMIT_FILING_CODE.get(
        str((elig or {}).get("plan_type") or "").upper(), DEFAULT_REMIT_FILING
    )
