"""Derive prescriptions, pharmacy claims and adherence from drug courses (``Therapy``).

A maintenance course is refilled until it is stopped, abandoned or the window ends: each refill
slips by a delay drawn from the member's adherence, some come early (and a few of those reject as
"refill too soon"), the prescription is renewed when its refills run out, and the member abandons
the drug at a monthly hazard that depends on the drug group.  Costs follow the plan's tier copay;
HDHP members pay the deductible first.  Every pharmacy claim carries an NDC that was marketed on the
fill date.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np

from .calibration import Calibration
from .directory import ProviderDirectory
from .drugs import DRUGS, Drug, InterimNdcDirectory, NdcDirectory
from .model import Member, Person, Plan, Therapy
from .reference import NCPDP_REJECT


@dataclass(slots=True)
class Order:
    oid: int
    member: int
    written: date
    drug: Drug
    quantity: float
    days_supply: int
    refills: int
    daw: str
    prescriber: int
    indication: str
    dx: str
    therapy: int
    acute: bool
    source: int | None


@dataclass(slots=True)
class Fill:
    n: int
    order: Order
    member: Member
    plan: Plan
    day: date
    fill_number: int
    quantity: float
    days_supply: int
    pharmacy: int
    mail: bool
    ndc: str
    status: str  # paid | rejected | reversed
    reject: str | None = None
    ingredient: int = 0
    fee: int = 0
    patient: int = 0
    paid: int = 0
    tier: int = 1
    txn: str = "B1"
    early: bool = False
    primary: bool = False
    pair: Fill | None = None


GROUP_OF = {
    "Biguanide antidiabetic": "antidiabetic", "Sulfonylurea antidiabetic": "antidiabetic", "DPP-4 inhibitor": "antidiabetic",
    "SGLT2 inhibitor": "antidiabetic", "GLP-1 receptor agonist": "antidiabetic", "GIP/GLP-1 receptor agonist": "antidiabetic",
    "Long-acting insulin": "insulin", "Rapid-acting insulin": "insulin",
    "ACE inhibitor": "ras", "Angiotensin receptor blocker": "ras",
    "HMG-CoA reductase inhibitor (statin)": "statin",
    "Inhaled corticosteroid/LABA": "inhaler", "Long-acting muscarinic antagonist": "inhaler",
    "SSRI antidepressant": "antidepressant", "Antidepressant": "antidepressant",
    "Calcium channel blocker": "antihypertensive", "Thiazide diuretic": "antihypertensive",
    "Thiazide-like diuretic": "antihypertensive", "Beta blocker": "antihypertensive",
    "Aldosterone antagonist": "antihypertensive",
}
ADHERENCE_GROUP = {"ras": "antihypertensive", "insulin": "antidiabetic"}
# groups whose proportion of days covered is reported (CMS Part D style)
PDC_GROUPS = ("statin", "ras", "antidiabetic", "antihypertensive", "inhaler", "antidepressant")


def _cents(x: float) -> int:
    return int(round(x * 100))


class RxBuilder:
    def __init__(self, persons: list[Person], plans: dict[str, Plan], directory: ProviderDirectory,
                 cal: Calibration, seed: int, start: date, end: date,
                 ndc: NdcDirectory | None = None, ind: Any = None, fam: Any = None) -> None:
        self.persons = persons
        self.plans = plans
        self.dir = directory
        self.cal = cal
        self.seed = seed
        self.start = start
        self.end = end
        self.ndc: NdcDirectory = ndc or InterimNdcDirectory()
        self.orders: list[Order] = []
        self.fills: list[Fill] = []
        self.ind = ind if ind is not None else {}  # medical accumulators, shared and extended here
        self.fam = fam if fam is not None else {}
        self.rx_oop: dict[tuple[int, int, str], int] = {}
        self.stats: dict[str, int] = {"uncovered_fills": 0, "no_ndc": 0}

    # ---- fills ------------------------------------------------------------------------------------
    def build(self) -> None:
        for p in self.persons:
            for ti, th in enumerate(p.therapies):
                rng = np.random.default_rng([self.seed, 61, p.member.idx, ti])
                self._series(p, th, ti, rng)
        self.fills.sort(key=lambda f: (f.day, f.member.idx, f.n))
        self._cost()

    def _covered(self, m: Member, day: date) -> bool:
        return m.span_on(day) is not None

    def _next_covered(self, m: Member, day: date) -> date | None:
        later = [s.start for s in m.spans if s.start > day]
        return min(later) if later else None

    def _order(self, p: Person, th: Therapy, ti: int, written: date, rng: np.random.Generator,
               prescriber: int | None = None) -> Order:
        drug = DRUGS[th.drug_key]
        qty = th.quantity if th.quantity is not None else _quantity(drug, th.days_supply)
        refills = 0 if drug.schedule == "II" else th.refills
        pid = prescriber if prescriber is not None else self.dir.pick(p, th.specialty, written)
        order = Order(len(self.orders) + 1, p.member.idx, written, drug, qty, th.days_supply, refills,
                      th.daw, pid, th.indication, th.dx, ti, th.acute, th.encounter_id)
        self.orders.append(order)
        return order

    def _series(self, p: Person, th: Therapy, ti: int, rng: np.random.Generator) -> None:
        m = p.member
        drug = DRUGS[th.drug_key]
        lob = m.lob
        group = GROUP_OF.get(drug.cls, "other")
        agroup = ADHERENCE_GROUP.get(group, group if group in self.cal.get("rx.adherence_beta") else "other")
        a, b = self.cal.get("rx.adherence_beta")[agroup]
        adh = float(rng.beta(a, b))
        ongoing = th.start < self.start
        lo, hi = self.cal.get("rx.written_to_fill_days")
        written = th.start
        stop = th.stop or (self.end + timedelta(days=1))
        if th.acute:
            if rng.random() < 0.05:
                return
            day = written + timedelta(days=int(rng.integers(lo, hi + 1)))
            self._fill_one(p, th, ti, self._order(p, th, ti, written, rng), day, 0, rng, primary=True)
            return
        generic = not drug.brand
        if not ongoing and rng.random() < self.cal.get("rx.primary_nonadherence")["generic" if generic else "brand"]:
            order = self._order(p, th, ti, written, rng)
            day = written + timedelta(days=int(rng.integers(lo, hi + 1)))
            if day <= self.end and self._covered(m, day) and rng.random() < 0.4:
                self._add_reject(p, order, day, 0, "75" if drug.pa else "76", rng)
            return
        order = self._order(p, th, ti, written, rng)
        if ongoing:
            day = self.start + timedelta(days=int(rng.integers(0, th.days_supply)))
        else:
            day = written + timedelta(days=int(rng.integers(lo, hi + 1)))
        fill_no = 0
        used = 0
        monthly_stop = self.cal.get("rx.monthly_discontinue")[
            group if group in self.cal.get("rx.monthly_discontinue") else "other"
        ]
        carry = 0
        while day <= self.end and day < stop:
            if not self._covered(m, day):
                nxt = self._next_covered(m, day)
                if nxt is None or rng.random() < 0.15:
                    break
                day = nxt + timedelta(days=int(rng.integers(0, 6)))
                continue
            if used > order.refills:
                if rng.random() > self.cal.get("rx.renew_prob"):
                    break
                order = self._order(p, th, ti, day - timedelta(days=int(rng.integers(0, 8))), rng,
                                    prescriber=order.prescriber)
                used = 0
                fill_no = 0
            fill = self._fill_one(p, th, ti, order, day, fill_no, rng, primary=(not ongoing and fill_no == 0))
            used += 1
            fill_no += 1
            if fill is None:
                break
            # next refill
            ds = order.days_supply
            if rng.random() < 1.0 - (1.0 - monthly_stop) ** (ds / 30.0):
                break  # abandons the drug
            if rng.random() < self.cal.get("rx.early_refill"):
                f = float(rng.uniform(0.55, 0.95))
                nxt_day = day + timedelta(days=max(1, int(ds * f)))
                if f < 0.75:
                    self._add_reject(p, order, nxt_day, fill_no, "79", rng)
                    nxt_day = day + timedelta(days=int(ds * 0.8) + int(rng.integers(0, 4)))
            else:
                delay = int(rng.exponential(max(0.01, (1.0 - adh) * ds * 0.55)))
                nxt_day = day + timedelta(days=ds + delay - carry)
                carry = 0
            day = nxt_day

    def _pharmacy(self, p: Person, mail: bool) -> int:
        key = "mail_pharm" if mail else "retail_pharm"
        if key not in p.flags:
            p.flags[key] = self.dir.pick_pharmacy(p, mail).idx
        return int(p.flags[key])

    def _fill_one(self, p: Person, th: Therapy, ti: int, order: Order, day: date, fill_no: int,
                  rng: np.random.Generator, primary: bool = False) -> Fill | None:
        m = p.member
        if day > self.end:
            return None
        span = m.span_on(day)
        if span is None:
            self.stats["uncovered_fills"] += 1
            return None
        plan = self.plans[span.plan_id]
        drug = order.drug
        mail = (order.days_supply >= 90 and drug.mail
                and rng.random() < min(0.9, self.cal.get("rx.mail_share")[m.lob] / 0.30))
        packs = self.ndc.packages(drug.key, day)
        if not packs:
            self.stats["no_ndc"] += 1
            return None
        want = order.quantity
        pack = min(packs, key=lambda r: abs(r.package_units - want))
        # first fill of a prior-authorisation drug may reject once for PA
        if drug.pa and fill_no == 0 and rng.random() < self.cal.get("rx.pa_first_reject"):
            self._add_reject(p, order, day, fill_no, "75", rng)
            if rng.random() >= self.cal.get("rx.pa_approved"):
                return None
            day = day + timedelta(days=int(rng.integers(1, 8)))
            if day > self.end or not self._covered(m, day):
                return None
        elif rng.random() < self.cal.get("rx.reject_rate")[m.lob] * 0.5:
            code = str(rng.choice(["70", "76", "88", "608"]))
            self._add_reject(p, order, day, fill_no, code, rng)
            day = day + timedelta(days=int(rng.integers(1, 5)))
            if day > self.end or not self._covered(m, day):
                return None
        fill = Fill(len(self.fills) + 1, order, m, plan, day, fill_no, want, order.days_supply,
                    self._pharmacy(p, mail), mail, pack.ndc, "paid", tier=min(drug.tier, 4), primary=primary)
        self.fills.append(fill)
        if rng.random() < self.cal.get("rx.reversal_rate"):
            rev = Fill(len(self.fills) + 1, order, m, plan, day + timedelta(days=int(rng.integers(7, 15))),
                       fill_no, want, order.days_supply, fill.pharmacy, mail, pack.ndc, "reversed",
                       tier=fill.tier, txn="B2", pair=fill)
            self.fills.append(rev)
            if rng.random() < 0.4:  # re-dispensed after the reversal
                self.fills.append(Fill(
                    len(self.fills) + 1, order, m, plan, rev.day + timedelta(days=int(rng.integers(1, 6))),
                    fill_no, want, order.days_supply, fill.pharmacy, mail, pack.ndc, "paid", tier=fill.tier,
                ))
        return fill

    def _add_reject(self, p: Person, order: Order, day: date, fill_no: int, code: str,
                    rng: np.random.Generator) -> None:
        span = p.member.span_on(day)
        if span is None or day > self.end:
            return
        plan = self.plans[span.plan_id]
        packs = self.ndc.packages(order.drug.key, day)
        if not packs:
            return
        pack = min(packs, key=lambda r: abs(r.package_units - order.quantity))
        self.fills.append(Fill(len(self.fills) + 1, order, p.member, plan, day, fill_no, order.quantity,
                               order.days_supply, self._pharmacy(p, False), False, pack.ndc, "rejected",
                               code, tier=min(order.drug.tier, 4)))

    # ---- costs ------------------------------------------------------------------------------------
    def _cost(self) -> None:
        from .claims import Accum

        fee = self.cal.get("rx.dispensing_fee")
        copay = self.cal.get("rx.copay_tier")
        for f in self.fills:
            if f.status != "paid":
                continue
            rng = np.random.default_rng([self.seed, 67, f.n])
            d = f.order.drug
            ingredient = _cents(d.cost30 * f.days_supply / 30.0 * float(rng.lognormal(0, 0.04)))
            if f.plan.lob == "medicaid":
                ingredient = int(ingredient * 0.78)
            f.ingredient = ingredient
            f.fee = _cents(fee[f.plan.lob]) if not f.mail else _cents(fee[f.plan.lob]) // 2
            total = f.ingredient + f.fee
            t1, t2, t3, t4 = copay[f.plan.lob]
            scale = 2.5 if f.days_supply >= 90 else 1.0
            if f.tier == 1:
                pay = _cents(t1 * scale)
            elif f.tier == 2:
                pay = _cents(t2 * scale)
            elif f.tier == 3:
                pay = _cents(t3 * scale)
            else:
                pay = int(total * t4) if t4 else _cents(t3)
            plan = f.plan
            ka = (f.member.idx, f.day.year, plan.plan_id)
            kf = (f.member.household, f.day.year, plan.lob, plan.plan_id)
            a = self.ind.setdefault(ka, Accum())
            fa = self.fam.setdefault(kf, Accum())
            ded = 0
            if plan.product == "HDHP":
                room = max(0, min(_cents(plan.deductible) - a.ded,
                                  _cents(plan.deductible * plan.family_multiple) - fa.ded))
                ded = min(total, room)
                pay = ded + int(round((total - ded) * plan.coinsurance))
            pay = min(pay, total)
            oop_room = max(0, min(_cents(plan.oop_max) - a.oop,
                                  _cents(plan.oop_max * plan.family_multiple) - fa.oop))
            if pay > oop_room:
                ded = min(ded, oop_room)
                pay = oop_room
            a.ded += ded
            fa.ded += ded
            a.oop += pay
            fa.oop += pay
            self.rx_oop[ka] = self.rx_oop.get(ka, 0) + pay
            f.patient = pay
            f.paid = total - pay
        for f in self.fills:
            if f.status == "reversed" and f.pair is not None:
                f.ingredient, f.fee, f.patient, f.paid = f.pair.ingredient, f.pair.fee, f.pair.patient, f.pair.paid

    def run(self) -> None:
        self.build()

    # ---- adherence ------------------------------------------------------------------------------
    def adherence_rows(self) -> list[dict[str, Any]]:
        by: dict[tuple[int, str, int], list[Fill]] = {}
        for f in self.fills:
            if f.status != "paid":
                continue
            group = GROUP_OF.get(f.order.drug.cls)
            if group not in PDC_GROUPS:
                continue
            by.setdefault((f.member.idx, group, f.day.year), []).append(f)
        rows = []
        for (idx, group, year), fills in sorted(by.items()):
            fills.sort(key=lambda f: f.day)
            m = fills[0].member
            period_start = fills[0].day
            period_end = min(date(year, 12, 31), self.end)
            if m.death and m.death < period_end:
                period_end = m.death
            last_span_end = max((s.end or self.end) for s in m.spans) if m.spans else period_end
            period_end = min(period_end, last_span_end)
            n_days = (period_end - period_start).days + 1
            if n_days < 91:
                continue  # CMS requires at least two fills and 91 days
            covered = np.zeros(n_days, dtype=bool)
            cursor = period_start
            early = 0
            for f in fills:
                begin = max(f.day, cursor)
                if f.day < cursor:
                    early += 1
                end_cov = begin + timedelta(days=f.days_supply)
                lo = (begin - period_start).days
                hi = min(n_days, (end_cov - period_start).days)
                if lo < n_days:
                    covered[lo:hi] = True
                cursor = end_cov
            days_covered = int(covered.sum())
            gaps = []
            run = 0
            for c in covered:
                if not c:
                    run += 1
                elif run:
                    gaps.append(run)
                    run = 0
            if run:
                gaps.append(run)
            last_cover = fills[-1].day + timedelta(days=fills[-1].days_supply)
            rows.append({
                "member_idx": idx, "year": year, "therapeutic_group": group, "first_fill_date": period_start,
                "period_days": n_days, "days_covered": days_covered, "pdc": days_covered / n_days,
                "fills": len(fills), "gap_days": int(n_days - days_covered), "max_gap_days": max(gaps) if gaps else 0,
                "early_refills": early, "abandoned": bool(last_cover + timedelta(days=60) < period_end),
            })
        return rows


def _quantity(drug: Drug, days: int) -> float:
    q = drug.qty30 * days / 30.0
    if drug.form.startswith(("AEROSOL", "POWDER")) or "INJECTION" in drug.form and drug.qty30 <= 1:
        return float(max(1, math.ceil(q if drug.qty30 > 1 else drug.qty30 * math.ceil(days / 30))))
    return float(round(q, 1))


def reject_text(code: str) -> str:
    return NCPDP_REJECT[code]
