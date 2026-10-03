"""834 benefit enrollment and maintenance (005010X220A1).

One loop 2000 per member (subscriber first, then dependents), one loop 2300 per coverage span
of the member. With ``maintenance_type_code="030"`` (the default) every member is reported as
an audit/compare record and a terminated span carries its end date; ``"auto"`` reports spans
that have ended as cancellations (024) and the others as additions (021).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, replace

from ..common import TableSet, d8
from ..contract import ContractError
from .core import EnvelopeOptions, Segments, clean, interchange, need, transaction
from .model import Row, provider_row

VERSION_834 = "005010X220A1"

COVERAGE_LEVEL = {"IND", "FAM", "E1D", "ESP", "ECH", "DEP", "CHD", "EMP", "SPC", "SPO"}


@dataclass(frozen=True, slots=True)
class EnrollmentOptions:
    """Values the tables do not carry. Defaults are clearly synthetic."""

    sponsor_name: str = "SYNTHETIC SPONSOR"
    sponsor_tax_id: str = "900000098"
    payer_name: str = "SYNTHETIC PAYER"
    payer_tax_id: str = "900000099"
    maintenance_type_code: str = "030"
    max_members_per_file: int = 50000
    insurance_line: str = "HLT"


def _members_by_family(ts: TableSet) -> list[Row]:
    members = ts.rows("member")
    order = {"18": 0}
    return sorted(
        members,
        key=lambda m: (
            str(m["subscriber_id"]),
            order.get(str(m["relationship_code"]), 1),
            str(m["member_id"]),
        ),
    )


def _maintenance(code: str, span_end: dt.date | None) -> str:
    if code == "auto":
        return "024" if span_end is not None else "021"
    if code not in ("030", "021", "024", "001"):
        raise ValueError(f"maintenance_type_code must be 030, 021, 024, 001 or auto, got {code!r}")
    return code


def _member_loop(
    seg: Segments,
    ts: TableSet,
    m: Row,
    spans: Sequence[Row],
    opts: EnrollmentOptions,
    env: EnvelopeOptions,
) -> None:
    where = f"member {m['member_id']}"
    subscriber = str(m["relationship_code"]) == "18"
    first_span = min(spans, key=lambda s: s["coverage_start"])
    last_span = max(spans, key=lambda s: s["coverage_start"])
    ends = [s["coverage_end"] for s in spans]
    closed = all(e is not None for e in ends)
    maint = _maintenance(opts.maintenance_type_code, max(ends) if closed else None)
    status = "A"
    seg.add(
        "INS",
        "Y" if subscriber else "N",
        clean(m["relationship_code"]),
        maint,
        "28" if maint == "024" else "XN" if maint != "030" else "",
        status,
        "",
        "",
        "FT" if subscriber else "",
    )
    seg.add("REF", "0F", clean(need(m["subscriber_id"], "subscriber id", where), limit=50))
    if last_span.get("group_id"):
        seg.add("REF", "1L", clean(last_span["group_id"], limit=50))
    seg.add("DTP", "356", "D8", d8(first_span["coverage_start"]))
    if closed:
        seg.add("DTP", "357", "D8", d8(max(ends)))
    seg.add(
        "NM1",
        "IL",
        "1",
        clean(m["last_name"]),
        clean(m["first_name"]),
        clean(m.get("middle_name"))[:1],
        "",
        "",
        "ZZ",
        clean(m["member_id"], limit=80),
    )
    if m.get("phone"):
        seg.add("PER", "IP", "", "HP", clean(m["phone"], limit=20))
    if m.get("address_line1") and m.get("city") and m.get("state") and m.get("zip"):
        lines = [clean(m["address_line1"], limit=55)]
        if m.get("address_line2"):
            lines.append(clean(m["address_line2"], limit=55))
        seg.add("N3", *lines)
        seg.add(
            "N4",
            clean(m["city"], limit=30),
            clean(m["state"], limit=2),
            "".join(ch for ch in str(m["zip"]) if ch.isdigit()),
        )
    seg.add(
        "DMG",
        "D8",
        d8(m["birth_date"]),
        clean(m["sex"] or "U"),
        clean(m.get("marital_status")),
    )
    for n, s in enumerate(sorted(spans, key=lambda s: s["coverage_start"]), start=1):
        level = clean(s.get("coverage_level"))
        if level and level not in COVERAGE_LEVEL:
            raise ContractError(f"{where}: coverage_level {level!r} is not an X12 coverage level")
        seg.add(
            "HD",
            _maintenance(opts.maintenance_type_code, s["coverage_end"]),
            "",
            clean(s.get("insurance_line") or opts.insurance_line),
            clean(s.get("plan_name") or s["plan_id"], limit=50),
            level,
        )
        seg.add("DTP", "348", "D8", d8(s["coverage_start"]))
        if s["coverage_end"] is not None:
            seg.add("DTP", "349", "D8", d8(s["coverage_end"]))
        if n == 1 and spans and s.get("pcp_npi"):
            pcp = provider_row(ts, s["pcp_npi"], where) if ts.has("provider") else None
            seg.add("LX", 1)
            if pcp is not None:
                qual = "1" if str(pcp.get("entity_type")) == "1" else "2"
                last = clean(pcp.get("last_name") if qual == "1" else pcp.get("org_name"))
                first = clean(pcp.get("first_name")) if qual == "1" else ""
                seg.add("NM1", "P3", qual, last, first, "", "", "", "XX", s["pcp_npi"], "72")


def build_enrollment(ts: TableSet, opts: EnrollmentOptions, env: EnvelopeOptions) -> list[str]:
    """One interchange (as text) per ``max_members_per_file`` members with coverage."""
    spans_by = ts.by("eligibility", "member_id")
    members = [m for m in _members_by_family(ts) if m["member_id"] in spans_by]
    if not members:
        return []
    chunks = [
        members[i : i + opts.max_members_per_file]
        for i in range(0, len(members), opts.max_members_per_file)
    ]
    files: list[str] = []
    for n, chunk in enumerate(chunks):
        e = replace(
            env,
            interchange_control=env.interchange_control + n,
            group_control=env.group_control + n,
        )
        files.append(_one_interchange(ts, chunk, spans_by, opts, e))
    return files


def _one_interchange(
    ts: TableSet,
    members: Sequence[Row],
    spans_by: dict[object, list[Row]],
    opts: EnrollmentOptions,
    env: EnvelopeOptions,
) -> str:
    seg = Segments(env.delimiters)
    created = env.created
    seg.add(
        "BGN",
        "00",
        f"ENR{env.interchange_control:09d}",
        created.strftime("%Y%m%d"),
        created.strftime("%H%M"),
        "",
        "",
        "",
        "4" if opts.maintenance_type_code == "030" else "2",
    )
    seg.add("N1", "P5", clean(opts.sponsor_name, limit=60), "FI", clean(opts.sponsor_tax_id))
    seg.add("N1", "IN", clean(opts.payer_name, limit=60), "FI", clean(opts.payer_tax_id))
    for m in members:
        _member_loop(seg, ts, m, spans_by[m["member_id"]], opts, env)
    block = transaction(
        seg.items,
        set_id="834",
        version=VERSION_834,
        control=env.first_set_control,
        delimiters=env.delimiters,
    )
    return interchange([block], functional_id="BE", version=VERSION_834, options=env)
