"""Calibrate the model from a customer's own tables.

``from_tables`` reads claims, eligibility and pharmacy tables in this domain's schema (a captured
shape of the customer's data loaded into the same columns) and returns a :class:`Calibration`
whose overrides replace the public figures: line-of-business mix, sex mix, denial and reject rates,
cesarean share, respiratory seasonality, and the coded prevalence of the chronic conditions by age
band.  Rates the data cannot support (too few observations) keep their public value."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from typing import Any

from .calibration import BANDS, Calibration, band

MIN_N = 30
PREVALENCE_PREFIXES: dict[str, tuple[str, ...]] = {
    "cond.dm": ("E10", "E11"),
    "cond.htn": ("I10", "I11", "I12", "I13"),
    "cond.lipid": ("E78",),
    "cond.ckd": ("N18",),
    "cond.asthma": ("J45",),
    "cond.depression": ("F32", "F33"),
}


def _age(birth: date, day: date) -> int:
    return day.year - birth.year - ((day.month, day.day) < (birth.month, birth.day))


def from_tables(tables: dict[str, Any], base: Calibration | None = None) -> Calibration:
    over: dict[str, Any] = {}
    members = {r["member_id"]: r for r in tables["member"].to_pylist()}
    n = len(members)
    if n >= MIN_N:
        lob = Counter(m["line_of_business"] for m in members.values())
        over["pop.lob_mix"] = {k: lob[k] / n for k in ("commercial", "ma", "medicaid") if lob[k]}
        fem: dict[str, float] = {}
        for k in ("commercial", "ma", "medicaid"):
            group = [m for m in members.values() if m["line_of_business"] == k]
            if len(group) >= MIN_N:
                fem[k] = sum(m["sex"] == "F" for m in group) / len(group)
        base_fem = (base or Calibration()).get("pop.female_share")
        over["pop.female_share"] = {**base_fem, **fem}
    claims = tables["medical_claim"].to_pylist()
    first = [r for r in claims if r["claim_version"] == 1 and not r["duplicate_of_claim_id"]]
    base_cal = base or Calibration()
    denial = dict(base_cal.get("claim.initial_denial"))
    for k in denial:
        group = [r for r in first if r["line_of_business"] == k]
        if len(group) >= 200:
            denial[k] = sum(r["claim_status"] == "denied" for r in group) / len(group)
    over["claim.initial_denial"] = denial
    deliveries = [r for r in first if r["drg_code"] in ("805", "806", "807", "786", "787", "788")]
    if len(deliveries) >= MIN_N:
        over["preg.cesarean_rate"] = sum(
            r["drg_code"] in ("786", "787", "788") for r in deliveries
        ) / len(deliveries)
    rx = tables["pharmacy_claim"].to_pylist()
    if len(rx) >= 500:
        reject = dict(base_cal.get("rx.reject_rate"))
        for k in reject:
            group = [r for r in rx if r["line_of_business"] == k and r["transaction_code"] == "B1"]
            if len(group) >= 200:
                reject[k] = sum(r["claim_status"] == "rejected" for r in group) / len(group)
        over["rx.reject_rate"] = reject
        paid = sum(r["claim_status"] == "paid" for r in rx)
        over["rx.reversal_rate"] = sum(r["claim_status"] == "reversed" for r in rx) / max(1, paid)
    # respiratory seasonality: first-listed J00-J22 codes by month, on the base table's scale
    dx = defaultdict(list)
    for r in tables["claim_diagnosis"].to_pylist():
        dx[r["claim_id"]].append(r["diagnosis_code"])
    months: Counter[int] = Counter()
    for r in first:
        c = dx[r["claim_id"]]
        if c and c[0].startswith(("J00", "J01", "J02", "J03", "J06", "J10", "J11", "J20", "J21")):
            months[r["service_from_date"].month] += 1
    if sum(months.values()) >= 12 * 20 and all(months[m] for m in range(1, 13)):
        mean = sum(months.values()) / 12
        base_w = base_cal.get("resp.season_weight")
        scale = sum(base_w.values()) / 12
        over["resp.season_weight"] = {m: months[m] / mean * scale for m in range(1, 13)}
    # coded prevalence by band: members with the diagnosis on any claim, among those with a year
    # of cover
    cover: dict[str, float] = defaultdict(float)
    for e in tables["eligibility"].to_pylist():
        end = e["coverage_end"] or date(2024, 12, 31)
        start = e["coverage_start"]
        cover[e["member_id"]] += max(0, (end - start).days)
    anchor = max((r["service_from_date"] for r in first), default=date(2024, 12, 31))
    seen: dict[str, set[str]] = defaultdict(set)
    for r in first:
        for code in dx[r["claim_id"]]:
            seen[r["member_id"]].add(code)
    for rate, prefixes in PREVALENCE_PREFIXES.items():
        num: Counter[str] = Counter()
        den: Counter[str] = Counter()
        for mid, m in members.items():
            if cover[mid] < 270:
                continue
            b = band(_age(m["birth_date"], anchor))
            den[b] += 1
            num[b] += any(c.startswith(prefixes) for c in seen[mid])
        if all(den[b] >= MIN_N for b in BANDS if b != "0-17"):
            over[rate] = {
                b: (num[b] / den[b] if den[b] >= MIN_N else base_cal.get(rate)[b]) for b in BANDS
            }
    return Calibration({**(base.overrides if base else {}), **over})
