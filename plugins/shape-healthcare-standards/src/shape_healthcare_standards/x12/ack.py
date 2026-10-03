"""277CA health care claim acknowledgment (005010X214).

One interchange per ``acknowledgment_date`` of the ``claim_acknowledgment`` table, with one
``ST*277`` transaction set. Loops:

* 2000A information source (the payer or clearinghouse), 2100A name, 2200A trace with the
  receipt (``DTP*050``) and process (``DTP*009``) dates;
* 2000B information receiver (the submitter), 2100B name, 2200B trace with the accepted and
  rejected totals of the whole transaction (``QTY*90`` / ``QTY*AA``, ``AMT*YU`` / ``AMT*YY``);
* 2000C billing provider, 2100C name, 2200C trace with the same totals for that provider
  (``QTY*QA`` / ``QTY*QC``);
* 2000D patient, 2100D name, 2200D claim status tracking (``TRN``, ``STC``, ``REF*1K``,
  ``DTP*472``) and 2220D service line status (``SVC``, ``STC``, ``REF*FJ``, ``DTP*472``).

The action code of a claim is ``WQ`` (accepted) or ``U`` (rejected). When the table leaves it
empty it is derived from the status category: ``A1``/``A2`` accepted, ``A3``/``A6``/``A7``/
``A8`` rejected. Any other category needs an explicit ``action_code``. The 2200B and 2200C
status carries ``WQ`` when any claim below it is accepted and ``U`` when all are rejected. No
code is checked against a registry; codes are copied through after a shape check.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from ..codes import is_claim_status_category, is_claim_status_code, is_entity_identifier
from ..common import TableSet, d8, money, money_text
from ..contract import ContractError, check_keys
from .core import EnvelopeOptions, Segments, clean, interchange, need, transaction
from .model import Row, member_row, provider_name_parts, provider_row

VERSION_277CA = "005010X214"
ACCEPTED = "WQ"
REJECTED = "U"
ZERO = Decimal("0.00")

# Category -> action code when ``action_code`` is empty.
DERIVED_ACTION = {
    "A1": ACCEPTED,
    "A2": ACCEPTED,
    "A3": REJECTED,
    "A6": REJECTED,
    "A7": REJECTED,
    "A8": REJECTED,
}


@dataclass(frozen=True, slots=True)
class AckOptions:
    """Values the acknowledgment table does not carry. Defaults are clearly synthetic.

    ``accepted_status`` and ``rejected_status`` are the ``(category, status code, entity)``
    written in the 2200B and 2200C status that summarises the claims below it.
    """

    source_name: str = "SYNTHETIC PAYER"
    source_id: str = "SYNTHPAYER"
    source_qualifier: str = "PI"
    receiver_name: str = "SYNTHETIC SUBMITTER"
    receiver_id: str = "SYNTHSUBMITTER"
    trace_id: str = "SYNTHTRACE"
    accepted_status: tuple[str, str, str] = ("A1", "19", "PR")
    rejected_status: tuple[str, str, str] = ("A3", "21", "PR")


@dataclass(slots=True)
class _Ack:
    row: Row
    where: str
    claim: Row
    action: str
    line: Row | None = None


def _where(row: Row) -> str:
    base = f"claim_acknowledgment claim {row['claim_id']}"
    return base if row["line_number"] is None else f"{base} line {row['line_number']}"


def _shape(row: Row, where: str) -> None:
    checks = (
        (
            "status_category_code",
            is_claim_status_category,
            "two uppercase letters or a letter and a digit",
        ),
        ("status_code", is_claim_status_code, "1 to 5 digits"),
        ("entity_identifier_code", is_entity_identifier, "2 or 3 uppercase letters or digits"),
    )
    for column, ok, shape in checks:
        value = row.get(column)
        if value is None and column == "entity_identifier_code":
            continue
        text = need(value, column, where)
        if not ok(text):
            raise ContractError(f"{where}: {column} {text!r} is malformed; expected {shape}")


def _action(row: Row, where: str) -> str:
    explicit = row.get("action_code")
    if explicit is not None:
        if explicit not in (ACCEPTED, REJECTED):
            raise ContractError(
                f"{where}: action_code {explicit!r} must be {ACCEPTED} or {REJECTED}"
            )
        action = str(explicit)
    else:
        category = str(row["status_category_code"])
        if category not in DERIVED_ACTION:
            raise ContractError(
                f"{where}: status category {category!r} has no action code rule; "
                f"set the action_code column ({ACCEPTED} or {REJECTED})"
            )
        action = DERIVED_ACTION[category]
    if row["line_number"] is not None and action != REJECTED:
        raise ContractError(
            f"{where}: action_code is {action}, but a service line status can only report "
            f"a rejection (action {REJECTED}) in 005010X214"
        )
    return action


def _prepare(ts: TableSet) -> list[_Ack]:
    if not ts.has("medical_claim"):
        raise ContractError("the 277CA writer needs the medical_claim table (companion table)")
    problems = check_keys("claim_acknowledgment", ts.tables["claim_acknowledgment"])
    if problems:
        raise ContractError("; ".join(problems))
    claims = ts.index("medical_claim", "claim_id")
    lines = {(r["claim_id"], r["line_number"]): r for r in ts.rows("medical_claim_line")}
    out: list[_Ack] = []
    for row in ts.rows("claim_acknowledgment"):
        where = _where(row)
        cid = need(row["claim_id"], "claim_id", where)
        need(row["acknowledgment_date"], "acknowledgment_date", where)
        _shape(row, where)
        if cid not in claims:
            raise ContractError(f"{where}: claim {cid!r} is not in the medical_claim table")
        line = None
        if row["line_number"] is not None:
            if not ts.has("medical_claim_line"):
                raise ContractError(f"{where}: a service line needs the medical_claim_line table")
            line = lines.get((cid, row["line_number"]))
            if line is None:
                raise ContractError(f"{where}: line is not in the medical_claim_line table")
        out.append(_Ack(row, where, claims[cid], _action(row, where), line))
    return out


def _status(seg: Segments, category: str, status: str, entity: str | None) -> str:
    return seg.comp(category, status, entity)


def _service_date(claim: Row) -> tuple[str, str]:
    start, end = claim["service_from_date"], claim["service_to_date"]
    if end is None or end == start:
        return "D8", d8(start)
    return "RD8", f"{d8(start)}-{d8(end)}"


def _totals(seg: Segments, claims: list[_Ack], qty: tuple[str, str]) -> None:
    accepted = [a for a in claims if a.action == ACCEPTED]
    rejected = [a for a in claims if a.action == REJECTED]
    seg.add("QTY", qty[0], len(accepted))
    seg.add("QTY", qty[1], len(rejected))
    seg.add("AMT", "YU", money_text(sum((money(a.claim["total_billed"]) for a in accepted), ZERO)))
    seg.add("AMT", "YY", money_text(sum((money(a.claim["total_billed"]) for a in rejected), ZERO)))


def _summary_status(
    seg: Segments, claims: list[_Ack], opts: AckOptions, when: dt.date | None
) -> None:
    """2200B status (with its date) or 2200C status (no date: STC02 is not used there)."""
    accepted = any(a.action == ACCEPTED for a in claims)
    cat, status, entity = opts.accepted_status if accepted else opts.rejected_status
    billed = sum((money(a.claim["total_billed"]) for a in claims), ZERO)
    seg.add(
        "STC",
        seg.comp(clean(cat), clean(status), clean(entity)),
        "" if when is None else d8(when),
        ACCEPTED if accepted else REJECTED,
        money_text(billed),
    )


def _provider_name(ts: TableSet, npi: str, where: str) -> tuple[str, str, str]:
    if ts.has("provider"):
        return provider_name_parts(provider_row(ts, npi, where))
    return "2", "BILLING PROVIDER", ""


def _patient_name(ts: TableSet, member_id: str, where: str) -> tuple[str, str, str]:
    if ts.has("member"):
        m = member_row(ts, member_id, where)
        return clean(m["last_name"]), clean(m["first_name"]), clean(m.get("middle_name"))[:1]
    return "UNKNOWN", "", ""


def _claim_segments(seg: Segments, head: _Ack, lines: list[_Ack]) -> None:
    claim, row = head.claim, head.row
    cid = clean(row["claim_id"], upper=False, limit=50)
    seg.add("TRN", "2", cid)
    seg.add(
        "STC",
        _status(
            seg,
            row["status_category_code"],
            row["status_code"],
            clean(row.get("entity_identifier_code")) or None,
        ),
        d8(row["acknowledgment_date"]),
        head.action,
        money_text(money(claim["total_billed"])),
    )
    if row.get("reference_number"):
        seg.add("REF", "1K", clean(row["reference_number"], upper=False, limit=50))
    fmt, text = _service_date(claim)
    seg.add("DTP", "472", fmt, text)
    inst = str(claim["claim_type"]) == "I"
    for ln in sorted(lines, key=lambda a: a.row["line_number"]):
        line = ln.line
        assert line is not None
        if line.get("procedure_code"):
            mods = [clean(line[f"modifier_{i}"]) for i in range(1, 5) if line.get(f"modifier_{i}")]
            code = seg.comp("HC", clean(line["procedure_code"]), *mods)
        else:
            code = seg.comp("NU", clean(line.get("revenue_code")))
        billed = money_text(money(line["billed_amount"]))
        seg.add("SVC", code, billed, "", clean(line.get("revenue_code")) if inst else "")
        seg.add(
            "STC",
            _status(
                seg,
                ln.row["status_category_code"],
                ln.row["status_code"],
                clean(ln.row.get("entity_identifier_code")) or None,
            ),
            d8(ln.row["acknowledgment_date"]),
            ln.action,
        )
        seg.add("REF", "FJ", str(ln.row["line_number"]))
        seg.add("DTP", "472", "D8", d8(line["service_date"]))


def _set(
    ts: TableSet,
    when: dt.date,
    acks: list[_Ack],
    opts: AckOptions,
    env: EnvelopeOptions,
) -> list[str]:
    seg = Segments(env.delimiters)
    claim_rows = [a for a in acks if a.row["line_number"] is None]
    line_rows = [a for a in acks if a.row["line_number"] is not None]
    by_claim = {a.row["claim_id"]: a for a in claim_rows}
    for ln in line_rows:
        if ln.row["claim_id"] not in by_claim:
            raise ContractError(
                f"{ln.where}: a service line acknowledgment needs a claim-level row "
                f"(null line_number) for claim {ln.row['claim_id']!r} on {when.isoformat()}"
            )
    trace = f"{opts.trace_id}-{d8(when)}"
    seg.add(
        "BHT",
        "0085",
        "08",
        clean(trace, upper=False),
        env.created.strftime("%Y%m%d"),
        env.created.strftime("%H%M"),
        "TH",
    )
    # 2000A / 2100A / 2200A
    seg.add("HL", 1, "", "20", 1)
    seg.add(
        "NM1",
        "PR",
        "2",
        clean(opts.source_name, limit=60),
        "",
        "",
        "",
        "",
        clean(opts.source_qualifier),
        clean(opts.source_id, limit=80),
    )
    received = min(
        (a.row["received_date"] for a in claim_rows if a.row["received_date"]), default=when
    )
    seg.add("TRN", "1", clean(trace, upper=False))
    seg.add("DTP", "050", "D8", d8(received))
    seg.add("DTP", "009", "D8", d8(when))
    # 2000B / 2100B / 2200B
    seg.add("HL", 2, 1, "21", 1)
    seg.add(
        "NM1",
        "41",
        "2",
        clean(opts.receiver_name, limit=60),
        "",
        "",
        "",
        "",
        "46",
        clean(opts.receiver_id, limit=80),
    )
    seg.add("TRN", "2", clean(f"BATCH-{d8(when)}", upper=False))
    _summary_status(seg, claim_rows, opts, when)
    _totals(seg, claim_rows, ("90", "AA"))
    hl = 2
    providers: dict[str, list[_Ack]] = {}
    for a in claim_rows:
        providers.setdefault(a.claim["billing_npi"], []).append(a)
    for npi in sorted(providers):
        group = providers[npi]
        hl += 1
        parent_b = hl
        qual, last, first = _provider_name(ts, npi, f"claim_acknowledgment billing provider {npi}")
        seg.add("HL", hl, 2, "19", 1)
        seg.add("NM1", "85", qual, last, first, "", "", "", "XX", clean(npi, limit=80))
        seg.add("TRN", "1", clean(npi, upper=False))
        _summary_status(seg, group, opts, None)
        _totals(seg, group, ("QA", "QC"))
        patients: dict[str, list[_Ack]] = {}
        for a in group:
            patients.setdefault(a.claim["member_id"], []).append(a)
        for member_id in sorted(patients):
            hl += 1
            last, first, middle = _patient_name(
                ts, member_id, f"claim_acknowledgment patient {member_id}"
            )
            seg.add("HL", hl, parent_b, "PT")
            seg.add("NM1", "QC", "1", last, first, middle, "", "", "MI", clean(member_id, limit=80))
            for a in sorted(patients[member_id], key=lambda x: str(x.row["claim_id"])):
                mine = [ln for ln in line_rows if ln.row["claim_id"] == a.row["claim_id"]]
                _claim_segments(seg, a, mine)
    return transaction(
        seg.items,
        set_id="277",
        version=VERSION_277CA,
        control=env.first_set_control,
        delimiters=env.delimiters,
    )


def build_acknowledgment(ts: TableSet, options: AckOptions, envelope: EnvelopeOptions) -> list[str]:
    """One interchange per ``acknowledgment_date``, in date order. Empty table: no interchange."""
    acks = _prepare(ts)
    by_date: dict[dt.date, list[_Ack]] = {}
    for a in acks:
        by_date.setdefault(a.row["acknowledgment_date"], []).append(a)
    files: list[str] = []
    for n, when in enumerate(sorted(by_date)):
        env = dataclasses.replace(
            envelope,
            interchange_control=envelope.interchange_control + n,
            group_control=envelope.group_control + n,
        )
        block = _set(ts, when, by_date[when], options, env)
        files.append(interchange([block], functional_id="HN", version=VERSION_277CA, options=env))
    return files
