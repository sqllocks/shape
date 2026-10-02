"""Clinician-readable member timelines (HTML).

One page per member: who they are, their coverage, the problem list with the diagnoses behind it,
risk scores, and then every visit, stay, lab, procedure and fill in date order with costs, grouped
by year.  Self-contained (inline CSS, no scripts), printable, light and dark.  ``blind=True`` removes
every identifier that could reveal where the record came from (member id, NPIs, names) for the
blinded review kit."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from html import escape
from pathlib import Path
from typing import Any

from .calibration import band
from .icd10cm import ICD10CM
from .reference import DRG, SPECIALTY
from .services import SERVICES

_CSS = """
:root{--bg:#fbfbfa;--fg:#1d2327;--mut:#5d6870;--line:#d9dfe3;--card:#fff;--acc:#0b5cad;--ok:#1b7f4d;--bad:#b3261e;--warn:#9a6700;--chip:#eef2f5}
@media (prefers-color-scheme:dark){:root{--bg:#14181b;--fg:#e6eaed;--mut:#9aa6ae;--line:#2c3439;--card:#1b2024;--acc:#6cb2ff;--ok:#5ec990;--bad:#ff8a80;--warn:#e3b341;--chip:#252c31}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1000px;margin:0 auto;padding:20px 16px 48px}h1{font-size:1.5rem;margin:.2em 0}h2{font-size:1.1rem;margin:1.6em 0 .5em;border-bottom:1px solid var(--line);padding-bottom:.2em}
.meta{color:var(--mut)}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 12px}.card b{display:block;font-size:.8rem;color:var(--mut);font-weight:600}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden;font-size:.9rem}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{background:var(--chip);font-size:.78rem;text-transform:uppercase;letter-spacing:.03em;color:var(--mut)}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}.chip{display:inline-block;background:var(--chip);border-radius:10px;padding:0 7px;font-size:.75rem;margin:0 3px 2px 0;white-space:nowrap}
.rx{border-left:3px solid var(--acc)}.ip{border-left:3px solid var(--bad)}.ed{border-left:3px solid var(--warn)}.dn{color:var(--bad)}.ok{color:var(--ok)}
.code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.85em}.wrap{overflow-x:auto}
@media print{body{background:#fff;color:#000}.card,table{break-inside:avoid}}
@media (max-width:640px){td,th{padding:5px 6px}}
"""


def _money(x: float | None) -> str:
    return "" if x is None else f"{x:,.2f}"


def _age(birth: date, day: date) -> int:
    return day.year - birth.year - ((day.month, day.day) < (birth.month, birth.day))


class TimelineIndex:
    """Per-member lookups over the generated tables (built once, used for many pages)."""

    def __init__(self, tables: dict[str, Any]) -> None:
        self.members = {r["member_id"]: r for r in tables["member"].to_pylist()}
        self.provider = {r["npi"]: r for r in tables["provider"].to_pylist()}
        self.drug = {r["ndc"]: r for r in tables["drug_reference"].to_pylist()}
        self.eligibility: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in tables["eligibility"].to_pylist():
            self.eligibility[r["member_id"]].append(r)
        self.claims: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
        for r in tables["medical_claim"].to_pylist():
            self.claims[r["member_id"]][r["claim_root_id"]].append(r)
        self.dx: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in tables["claim_diagnosis"].to_pylist():
            self.dx[r["claim_id"]].append(r)
        self.lines: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in tables["medical_claim_line"].to_pylist():
            self.lines[r["claim_id"]].append(r)
        self.pcs: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in tables["claim_procedure"].to_pylist():
            self.pcs[r["claim_id"]].append(r)
        self.rx: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in tables["pharmacy_claim"].to_pylist():
            self.rx[r["member_id"]].append(r)
        self.risk: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in tables["member_risk"].to_pylist():
            self.risk[r["member_id"]].append(r)
        self.adherence: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in tables["rx_adherence"].to_pylist():
            self.adherence[r["member_id"]].append(r)
        self.plans = {r["plan_id"]: r for r in tables["plan"].to_pylist()}


def _dx_text(code: str) -> str:
    icd = ICD10CM.get(code)
    return f"{code} {icd.desc}" if icd else code


def _provider(ix: TimelineIndex, npi: str | None, blind: bool) -> str:
    if not npi or npi not in ix.provider:
        return ""
    p = ix.provider[npi]
    name = SPECIALTY[p["specialty"]].name if p["specialty"] in SPECIALTY else p["specialty"]
    return escape(name if blind else f"{p['name']} · {name} · NPI {npi}")


def render_member(ix: TimelineIndex, member_id: str, *, blind: bool = False, label: str | None = None) -> str:
    m = ix.members[member_id]
    title = label or (f"Member {member_id}" if not blind else "Patient")
    rows: list[tuple[date, int, str]] = []
    problems: dict[str, list[Any]] = {}
    totals: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    final_status: dict[str, str] = {}
    for root, versions in ix.claims[member_id].items():
        versions = sorted(versions, key=lambda r: r["claim_version"])
        first = versions[0]
        last = [v for v in versions if v["claim_frequency_code"] != "8"][-1] if any(v["claim_frequency_code"] != "8" for v in versions) else versions[-1]
        dup = [v for v in versions if v["duplicate_of_claim_id"]]
        if dup:
            continue
        lifecycle = []
        for v in versions:
            tag = {"1": "original", "7": "replacement", "8": "void"}[v["claim_frequency_code"]]
            if v["claim_version"] == 1 and v["claim_status"] == "denied":
                lifecycle.append(f"denied (CARC {v['denial_carc']}{('/' + v['denial_rarc']) if v['denial_rarc'] else ''})")
            elif v["claim_version"] > 1:
                lifecycle.append(f"{tag}: {v['claim_status']}")
        voided = any(v["claim_frequency_code"] == "8" for v in versions) and not any(v["claim_version"] == 3 for v in versions)
        paid = last["claim_status"] in ("paid",) and not voided
        day = first["admission_date"] or first["service_from_date"]
        lines = ix.lines[first["claim_id"]]
        dxs = sorted(ix.dx[first["claim_id"]], key=lambda r: r["sequence"])
        for d in dxs:
            e = problems.setdefault(d["diagnosis_code"], [first["service_from_date"], first["service_from_date"], 0])
            e[0], e[1] = min(e[0], first["service_from_date"]), max(e[1], first["service_from_date"])
            e[2] += 1
        kind = first["facility_type"] or "professional"
        css = "ip" if kind == "inpatient" else "ed" if kind == "emergency" else ""
        what = []
        for ln in lines:
            s = SERVICES.get(ln["service_key"])
            desc = s.desc if s else ln["service_key"]
            code = ln["procedure_code"] if ln["code_system"] != "SHAPE-SVC" else ""
            units = f" ×{ln['units']}" if ln["units"] and ln["units"] > 1 else ""
            what.append(f"{escape(desc)}{units}" + (f" <span class='code'>[{escape(code)}]</span>" if code else ""))
        pcs = ", ".join(f"<span class='code'>{r['procedure_code']}</span>" for r in ix.pcs[first["claim_id"]])
        stay = ""
        if kind == "inpatient":
            drg = DRG.get(first["drg_code"] or "")
            stay = (f"<div><b>Inpatient stay</b> {first['admission_date']} → {first['discharge_date']} "
                    f"({first['length_of_stay']} d) · DRG <span class='code'>{first['drg_code']}</span> "
                    f"{escape(drg.title) if drg else ''}"
                    f"{' · died in hospital' if first['patient_discharge_status'] == '20' else ''}</div>")
            if pcs:
                stay += f"<div>Procedures (ICD-10-PCS): {pcs}</div>"
        dx_html = "".join(
            f"<span class='chip' title='{escape(_dx_text(d['diagnosis_code']))}'><span class='code'>{d['diagnosis_code']}</span>"
            f"{'·' + d['present_on_admission'] if d['present_on_admission'] else ''}</span>" for d in dxs
        )
        dx_names = "; ".join(escape(_dx_text(d["diagnosis_code"])) for d in dxs[:3])
        status = ("<span class='ok'>paid</span>" if paid else "<span class='dn'>not paid</span>")
        chips = "".join(f"<span class='chip'>{escape(c)}</span>" for c in lifecycle)
        money = ""
        if paid:
            money = (f"allowed {_money(last['allowed_amount'])} · plan {_money(last['paid_amount'])} · "
                     f"member {_money(last['member_responsibility'])}")
            totals[first["service_from_date"].year][0] += last["allowed_amount"]
            totals[first["service_from_date"].year][1] += last["paid_amount"]
            totals[first["service_from_date"].year][2] += last["member_responsibility"]
        prov = _provider(ix, first["rendering_provider_npi"] or first["billing_provider_npi"], blind)
        auth = f" · prior auth {escape(first['prior_auth_number'])}" if first["prior_auth_number"] and not blind else (" · prior authorization on file" if first["prior_auth_number"] else "")
        label_k = {"professional": "Professional", "inpatient": "Inpatient", "emergency": "Emergency dept", "outpatient_hospital": "Hospital outpatient",
                   "asc": "Surgery center", "dialysis": "Dialysis"}[kind]
        html = (f"<tr class='{css}'><td class='n'>{day}</td><td>{label_k}<br><span class='meta'>{prov}</span></td>"
                f"<td>{stay}<div>{'; '.join(what)}</div><div class='meta'>{dx_names}</div><div>{dx_html}</div></td>"
                f"<td>{status}<br>{chips}<div class='meta'>{money}{auth}</div></td></tr>")
        rows.append((day, 0, html))
        final_status[root] = last["claim_status"]
    for f in ix.rx[member_id]:
        if f["claim_status"] == "rejected":
            status = f"<span class='dn'>rejected {escape(f['reject_code'])}</span> {escape(f['reject_description'] or '')}"
        elif f["claim_status"] == "reversed":
            status = "<span class='dn'>reversed</span>"
        else:
            status = f"<span class='ok'>paid</span> patient {_money(f['patient_pay'])} · plan {_money(f['plan_paid'])}"
        drug = ix.drug.get(f["ndc"])
        name = f"{f['drug_name']} {f['strength']} {f['dosage_form'].lower()}"
        sched = f" · C-{f['dea_schedule']}" if f["dea_schedule"] else ""
        bg = {"B": "brand", "G": "generic"}[f["brand_generic"]]
        prescriber = _provider(ix, f["prescriber_npi"], blind)
        rows.append((f["fill_date"], 1, (
            f"<tr class='rx'><td class='n'>{f['fill_date']}</td><td>Pharmacy<br><span class='meta'>{f['pharmacy_type']}</span></td>"
            f"<td><b>{escape(name)}</b> · {f['quantity_dispensed']:g} for {f['days_supply']} days · {bg} · tier {f['formulary_tier']}{sched}"
            f"<div class='meta'>{escape(f['therapeutic_class'])} · fill #{f['fill_number']} · written {f['date_written']}"
            f"{' · prescriber ' + prescriber if prescriber else ''}</div></td><td>{status}</td></tr>")))
        del drug
    rows.sort(key=lambda r: (r[0], r[1]))
    last_day = max((r[0] for r in rows), default=date(2024, 12, 31))
    age = _age(m["birth_date"], last_day)
    plans = []
    for e in sorted(ix.eligibility[member_id], key=lambda e: e["effective_date"]):
        p = ix.plans[e["plan_id"]]
        plans.append(f"<tr><td>{e['effective_date']}</td><td>{e['termination_date'] or 'ongoing'}</td><td>{escape(e['line_of_business'])} {escape(e['product'])}</td>"
                     f"<td>{escape(e['termination_reason'] or '')}</td><td class='n'>{_money(p['deductible'])}</td><td class='n'>{_money(p['oop_max'])}</td></tr>")
    prob_rows = "".join(
        f"<tr><td class='code'>{c}</td><td>{escape(ICD10CM[c].desc) if c in ICD10CM else ''}</td><td>{v[0]}</td><td>{v[1]}</td><td class='n'>{v[2]}</td></tr>"
        for c, v in sorted(problems.items(), key=lambda kv: -kv[1][2])[:30]
    )
    risk = "".join(f"<tr><td>{r['risk_year']}</td><td class='n'>{r['raf_concurrent']}</td><td>{escape(r['hcc_concurrent'] or '—')}</td></tr>" for r in sorted(ix.risk[member_id], key=lambda r: r["risk_year"]))
    adh = "".join(f"<tr><td>{r['measurement_year']}</td><td>{escape(r['therapeutic_group'])}</td><td class='n'>{r['pdc']:.2f}</td><td class='n'>{r['gap_days']}</td><td>{'abandoned' if r['abandoned_flag'] else ''}</td></tr>" for r in sorted(ix.adherence[member_id], key=lambda r: (r["measurement_year"], r["therapeutic_group"])))
    cost = "".join(f"<tr><td>{y}</td><td class='n'>{_money(v[0])}</td><td class='n'>{_money(v[1])}</td><td class='n'>{_money(v[2])}</td></tr>" for y, v in sorted(totals.items()))
    body_rows = []
    year = None
    for day, _, html in rows:
        if day.year != year:
            year = day.year
            body_rows.append(f"<tr><th colspan='4'>{year}</th></tr>")
        body_rows.append(html)
    ident = (f"<div class='card'><b>Age / sex</b>{age} / {escape(m['sex'])}</div>" if blind else
             f"<div class='card'><b>Member</b>{escape(m['first_name'])} {escape(m['last_name'])}<br><span class='code'>{escape(member_id)}</span></div>"
             f"<div class='card'><b>Age / sex</b>{age} / {escape(m['sex'])} (born {m['birth_date']})</div>"
             f"<div class='card'><b>Residence</b>{escape(m['city'])}, {escape(m['state'])} {escape(m['zip'])}</div>"
             f"<div class='card'><b>Subscriber / relationship</b><span class='code'>{escape(m['subscriber_id'])}-{escape(m['member_suffix'])}</span> · code {escape(m['relationship_code'])}</div>")
    if blind:
        ident += f"<div class='card'><b>Coverage</b>{escape(m['line_of_business'])}</div>"
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{escape(title)} timeline</title><style>{_CSS}</style></head><body><main>"
            f"<h1>{escape(title)}</h1><p class='meta'>Member timeline: diagnoses, procedures, fills and costs in date order.</p>"
            f"<div class='grid'>{ident}</div>"
            f"<h2>Coverage</h2><div class='wrap'><table><tr><th>From</th><th>To</th><th>Plan</th><th>Ended because</th><th class='n'>Deductible</th><th class='n'>OOP max</th></tr>{''.join(plans)}</table></div>"
            f"<h2>Problem list (diagnoses seen on claims)</h2><div class='wrap'><table><tr><th>Code</th><th>Description</th><th>First</th><th>Last</th><th class='n'>Claims</th></tr>{prob_rows}</table></div>"
            f"<h2>Risk</h2><div class='wrap'><table><tr><th>Year</th><th class='n'>RAF</th><th>HCC categories</th></tr>{risk}</table></div>"
            f"<h2>Timeline</h2><div class='wrap'><table><tr><th>Date</th><th>Setting</th><th>What happened</th><th>Outcome</th></tr>{''.join(body_rows)}</table></div>"
            f"<h2>Cost by year (paid claims)</h2><div class='wrap'><table><tr><th>Year</th><th class='n'>Allowed</th><th class='n'>Plan paid</th><th class='n'>Member</th></tr>{cost}</table></div>"
            f"<h2>Medication adherence</h2><div class='wrap'><table><tr><th>Year</th><th>Group</th><th class='n'>PDC</th><th class='n'>Gap days</th><th></th></tr>{adh}</table></div>"
            f"</main></body></html>")


def write_timelines(tables: dict[str, Any], directory: str | Path, member_ids: list[str], *, blind: bool = False) -> list[Path]:
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    ix = TimelineIndex(tables)
    paths = []
    for mid in member_ids:
        path = out / f"{mid}.html"
        path.write_text(render_member(ix, mid, blind=blind), encoding="utf-8")
        paths.append(path)
    return paths


def pick_rich_members(tables: dict[str, Any], n: int = 10) -> list[str]:
    """Members worth reading: the most encounters first, across lines of business."""
    counts: dict[str, int] = defaultdict(int)
    for r in tables["medical_claim"].to_pylist():
        if r["claim_version"] == 1 and not r["duplicate_of_claim_id"]:
            counts[r["member_id"]] += 1
    lob = {r["member_id"]: r["line_of_business"] for r in tables["member"].to_pylist()}
    by_lob: dict[str, list[str]] = defaultdict(list)
    for mid, c in sorted(counts.items(), key=lambda kv: -kv[1]):
        by_lob[lob[mid]].append(mid)
    out: list[str] = []
    while len(out) < n and any(by_lob.values()):
        for k in sorted(by_lob):
            if by_lob[k] and len(out) < n:
                out.append(by_lob[k].pop(0))
    return out


__all__ = ["TimelineIndex", "band", "pick_rich_members", "render_member", "write_timelines"]
