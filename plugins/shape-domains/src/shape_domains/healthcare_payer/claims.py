"""Derive medical claims from clinical encounters.

Encounters become claim *drafts* (one professional claim per rendering specialty, one institutional
claim per facility stay or visit).  A lifecycle pass decides what happens to each draft (paid,
denied with CARC/RARC, resubmitted, replaced, voided, duplicated); money is then priced in service
date order so deductible and out-of-pocket accumulators are exact.  All money is integer cents
until the tables are written.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np

from .byo import LicensedTables
from .calibration import Calibration
from .clinical import READMIT_DRGS  # noqa: F401  (re-exported for reports)
from .directory import ProviderDirectory, region_of
from .model import Encounter, Member, Person, Plan, Provider
from .reference import CARC, DENIAL_MIX, DRG, PRIMARY_CARE, RARC
from .services import SERVICES, Service, code_for

PREVENTIVE_DX = ("Z00", "Z12", "Z23", "Z13", "Z39")
FACILITY_SETTINGS = {"ed", "inpatient", "outpatient_hospital", "asc", "dialysis"}
POS_OF = {"office": "11", "urgent": "20", "ed": "23", "inpatient": "21", "outpatient_hospital": "22",
          "asc": "24", "dialysis": "65", "lab": "81", "imaging": "11", "transport": "41", "dme": "12"}
FACILITY_KINDS = ("facility",)


@dataclass(slots=True)
class Line:
    svc: Service
    units: int
    day: date
    rendering: Provider | None
    dx_ptr: tuple[int, ...]
    modifiers: tuple[str, ...] = ()
    base: int = 0  # contracted allowed amount, cents (before cost sharing and denial)
    billed: int = 0
    allowed: int = 0
    paid: int = 0
    copay: int = 0
    coins: int = 0
    ded: int = 0
    cob: int = 0
    denial: tuple[str, str | None] | None = None


@dataclass(slots=True)
class Draft:
    n: int
    enc: Encounter
    member: Member
    ctype: str  # P or I
    facility_type: str | None
    frm: date
    to: date
    lines: list[Line]
    dx: list[tuple[str, str]]
    billing: Provider
    rendering: Provider | None
    pos: str | None
    plan: Plan
    pcs: list[str] = field(default_factory=list)
    drg: str | None = None
    status_code: str | None = None
    pa_required: bool = False
    oon: bool = False
    emergency: bool = False
    inpatient_days: int = 0
    versions: list[dict[str, Any]] = field(default_factory=list)
    final_paid: bool = False
    pa_missing: bool = False
    held_for_cob: bool = False


@dataclass(slots=True)
class Auth:
    auth_id: str
    member: int
    plan_id: str
    service_key: str
    code: str
    requested: date
    decided: date
    status: str
    valid_from: date
    valid_to: date
    units: int
    draft: int
    retro: bool = False


@dataclass(slots=True)
class Accum:
    ded: int = 0
    oop: int = 0


def _cents(x: float) -> int:
    return int(round(x * 100))


class ClaimsBuilder:
    def __init__(self, persons: list[Person], plans: dict[str, Plan], directory: ProviderDirectory,
                 cal: Calibration, seed: int, start: date, end: date,
                 licensed: LicensedTables | None = None) -> None:
        self.persons = persons
        self.plans = plans
        self.dir = directory
        self.cal = cal
        self.seed = seed
        self.start = start
        self.end = end
        self.lic = licensed or LicensedTables()
        self.drafts: list[Draft] = []
        self.auths: list[Auth] = []
        self.stats: dict[str, int] = {"uncovered_encounters": 0, "dropped_lines": 0}
        self._rng = np.random.default_rng([seed, 41])

    # ---- 1. drafts ----------------------------------------------------------------------------
    def build(self) -> None:
        encs = [(e, p) for p in self.persons for e in p.encounters]
        encs.sort(key=lambda t: (t[0].admit or t[0].day, t[0].member, t[0].eid))
        for enc, person in encs:
            m = person.member
            day = enc.admit or enc.day
            span = m.span_on(day)
            if span is None:
                self.stats["uncovered_encounters"] += 1
                continue
            plan = self.plans[span.plan_id]
            self._drafts_for(enc, person, plan)

    def _lines(self, person: Person, enc: Encounter, services: list[Any], rendering: Provider | None,
               day: date) -> list[Line]:
        out = []
        m = person.member
        age = m.age(day)
        for s in services:
            svc = SERVICES[s.key]
            if (svc.sex and svc.sex != m.sex) or not svc.age_min <= age <= svc.age_max or day < svc.effective:
                self.stats["dropped_lines"] += 1
                continue
            mods: tuple[str, ...] = s.modifiers
            if enc.setting == "telehealth" and svc.key == "EM_TELE_EST":
                mods = ("GT",)
            ptr = tuple(p for p in s.dx_ptr if p <= len(enc.dx)) or (1,)
            if svc.kind in ("pro",) and len(enc.dx) > 1 and svc.key.startswith("EM_"):
                ptr = tuple(range(1, min(len(enc.dx), 4) + 1))
            out.append(Line(svc, max(1, s.units), day + timedelta(days=s.day_offset), rendering, ptr, mods))
        return out

    def _provider(self, person: Person, specialty: str, day: date, default: int | None) -> Provider:
        if default is not None and self.dir.info(default).specialty == specialty:
            return self.dir.info(default)
        return self.dir.info(self.dir.pick(person, specialty, day))

    def _drafts_for(self, enc: Encounter, person: Person, plan: Plan) -> None:
        m = person.member
        day = enc.admit or enc.day
        facility_lines: list[Any] = []
        pro_groups: dict[str, list[Any]] = {}
        for s in enc.services:
            svc = SERVICES[s.key]
            to_facility = (
                enc.setting in FACILITY_SETTINGS and svc.kind in ("facility", "drug", "lab", "imaging")
                and enc.setting != "inpatient"
            ) or svc.kind == "facility"
            if enc.setting == "inpatient":
                to_facility = False
            if to_facility:
                facility_lines.append(s)
            else:
                pro_groups.setdefault(s.specialty or enc.specialty, []).append(s)
        # institutional claim
        if enc.setting in FACILITY_SETTINGS:
            fac = self.dir.info(enc.facility if enc.facility is not None else self.dir.pick(person, "hospital", day))
            if enc.setting == "ed":
                fac = self.dir.info(self.dir.pick(person, "hospital", day))
            if enc.setting == "dialysis":
                fac = self.dir.info(enc.facility if enc.facility is not None else self.dir.pick(person, "dialysis_center", day))
            if enc.setting == "inpatient":
                lines = self._inpatient_lines(enc, person, plan)
            else:
                extra = {"asc": "ASC_FACILITY", "outpatient_hospital": None}.get(enc.setting)
                svcs = list(facility_lines)
                if extra:
                    from .model import Svc
                    svcs = [Svc(extra)] + svcs
                if enc.setting == "outpatient_hospital":
                    from .model import Svc
                    kind = "OP_INFUSION_FACILITY" if any(
                        SERVICES[s.key].kind == "drug" or s.key == "CHEMO_ADMIN" for s in enc.services
                    ) else "OP_PROCEDURE_FACILITY"
                    svcs = [Svc(kind)] + svcs
                lines = self._lines(person, enc, svcs, None, enc.day)
            if lines:
                to = enc.discharge or enc.day
                fdraft = Draft(
                    len(self.drafts), enc, m, "I", {"inpatient": "inpatient", "ed": "emergency",
                    "outpatient_hospital": "outpatient_hospital", "asc": "asc", "dialysis": "dialysis"}[enc.setting],
                    day, to, lines, enc.dx, fac, self.dir.info(enc.provider) if enc.provider is not None else None,
                    None, plan, list(enc.pcs), enc.drg, enc.status_code if enc.setting == "inpatient" else None,
                    emergency=enc.emergency, inpatient_days=((enc.discharge - enc.admit).days if enc.discharge and enc.admit else 0),
                )
                fdraft.pa_required = enc.prior_auth or any(l.svc.pa for l in lines)
                fdraft.oon = (not fac.network) and not enc.emergency
                self.drafts.append(fdraft)
        # professional claims
        for specialty, svcs in pro_groups.items():
            rendering = self._provider(person, specialty, day, enc.provider if specialty == enc.specialty else None)
            if rendering.kind == "organization" and rendering.billing_npi is None:
                billing = rendering
            else:
                billing = self._billing_org(rendering)
            lines = self._lines(person, enc, svcs, rendering, enc.day)
            if not lines:
                continue
            pos = POS_OF.get(enc.setting, "11")
            if enc.setting == "telehealth":
                pos = "10" if enc.day >= date(2022, 1, 1) and self._rng.random() < 0.6 else "02"
            if enc.setting in ("outpatient_hospital",) and self._rng.random() < 0.3:
                pos = "19"
            for ln in lines:
                if enc.setting in ("office", "imaging") and ln.svc.kind in ("imaging",) and pos == "11":
                    pos = "22"
            if pos not in _allowed_pos(lines):
                allowed = _allowed_pos(lines)
                pos = allowed[0] if allowed else pos
            ddraft = Draft(
                len(self.drafts), enc, m, "P", None, enc.day, enc.day, lines, enc.dx, billing, rendering,
                pos, plan, [], None, None, emergency=enc.emergency,
            )
            ddraft.pa_required = enc.prior_auth or any(l.svc.pa for l in lines)
            ddraft.oon = (not rendering.network) and not enc.emergency
            self.drafts.append(ddraft)

    def _billing_org(self, rendering: Provider) -> Provider:
        if rendering.billing_npi is None:
            return rendering
        for p in self.dir.providers:
            if p.npi == rendering.billing_npi:
                return p
        return rendering

    def _inpatient_lines(self, enc: Encounter, person: Person, plan: Plan) -> list[Line]:
        from .model import Svc
        days = max(1, ((enc.discharge - enc.admit).days if enc.discharge and enc.admit else 1))
        surgical = bool(enc.pcs) and DRG[enc.drg or "795"].kind == "P"
        svcs = [Svc("INPT_ROOM_BOARD", units=days), Svc("INPT_ANCILLARY")]
        if surgical:
            svcs.append(Svc("INPT_OPERATING_ROOM"))
        return self._lines(person, enc, svcs, None, enc.admit or enc.day)

    # ---- 2. contract pricing ---------------------------------------------------------------------
    def _price_all(self) -> None:
        mult = self.cal.get("price.payer_multiplier")
        region = self.cal.get("price.region_factor")
        markup = self.cal.get("price.billed_markup")
        base_rate = float(self.cal.get("price.drg_base_rate"))
        for d in self.drafts:
            rng = np.random.default_rng([self.seed, 43, d.n])
            lob = d.plan.lob
            reg = region[region_of(d.member.state)]
            tier = d.billing.price_tier
            if d.facility_type == "inpatient":
                fac_mult = mult["commercial_facility"] if lob == "commercial" else mult["ma"] if lob == "ma" else mult["medicaid"]
                drg = DRG[d.drg or "795"]
                total = drg.weight * base_rate * reg * fac_mult * tier * float(rng.lognormal(0, float(self.cal.get("price.drg_case_sigma"))))
                over = max(0, d.inpatient_days - 2.0 * drg.gmlos)
                total += over * float(self.cal.get("price.outlier_per_diem")) * reg * fac_mult
                weights = {"INPT_ROOM_BOARD": 0.45, "INPT_ANCILLARY": 0.40, "INPT_OPERATING_ROOM": 0.15}
                keys = [l.svc.key for l in d.lines]
                wsum = sum(weights[k] for k in keys)
                remaining = _cents(total)
                for i, ln in enumerate(d.lines):
                    share = weights[ln.svc.key] / wsum
                    ln.base = remaining if i == len(d.lines) - 1 else int(round(_cents(total) * share))
                    remaining -= ln.base
                continue
            for ln in d.lines:
                is_fac = (ln.svc.kind in ("facility", "imaging")
                          or d.facility_type is not None and ln.svc.kind in ("drug", "lab"))
                if lob != "commercial":
                    m = mult[lob]
                elif ln.svc.kind == "lab":
                    m = mult["commercial_lab"]
                else:
                    m = mult["commercial_facility"] if is_fac else mult["commercial_pro"]
                v = ln.svc.base * ln.units * m * reg * tier * float(rng.lognormal(0, 0.06))
                if d.oon and lob == "commercial":
                    v *= 0.9
                ln.base = max(1, _cents(v))
        for d in self.drafts:
            rng = np.random.default_rng([self.seed, 47, d.n])
            k = markup[d.plan.lob]
            for ln in d.lines:
                ln.billed = max(ln.base, _cents(ln.base / 100 * k * float(rng.uniform(0.9, 1.15))))

    # ---- 3. lifecycle ---------------------------------------------------------------------------
    def _soft_denial(self, d: Draft, rng: np.random.Generator) -> tuple[str, str | None]:
        options = [(c, r, w) for c, r, w in DENIAL_MIX if c not in ("197", "242", "18", "22")
                   and not (c == "97" and len(d.lines) < 2)]
        total = sum(w for _, _, w in options)
        x = rng.random() * total
        acc = 0.0
        for c, r, w in options:
            acc += w
            if x < acc:
                return c, r
        return options[-1][0], options[-1][1]

    def lifecycle(self) -> None:
        cal = self.cal
        lag_lo, lag_hi = cal.get("claim.lag_days")
        for d in self.drafts:
            rng = np.random.default_rng([self.seed, 53, d.n])
            lob = d.plan.lob
            service_end = d.to
            lag = int(rng.integers(lag_lo, lag_hi + 1)) if d.ctype == "P" else int(rng.integers(5, 21))
            received = service_end + timedelta(days=lag)
            adjudicated = received + timedelta(days=int(rng.integers(2, 22)))
            hard: tuple[str, str | None] | None = None
            # prior authorisation
            if d.pa_required:
                if rng.random() < cal.get("claim.pa_missing"):
                    d.pa_missing = True
                    hard = ("197", "N130")
                else:
                    self._auth(d, rng, retro=False)
            if hard is None and d.oon and d.plan.product in ("HMO", "EPO", "MA-HMO", "MCO"):
                hard = ("242", None)
            if hard is None and d.member.cob and rng.random() < 0.15:
                hard = ("22", "N4")
                d.held_for_cob = True
            soft = None
            if hard is None and rng.random() < cal.get("claim.initial_denial")[lob]:
                soft = self._soft_denial(d, rng)
            denial = hard or soft
            v1 = {"version": 1, "freq": "1", "received": received, "adjudicated": adjudicated,
                  "paid_date": None, "status": "denied" if denial else "paid", "denial": denial, "kind": "original"}
            d.versions.append(v1)
            if denial:
                d.final_paid = False
                resub = rng.random() < cal.get("claim.resubmit_share")
                if denial[0] in ("242",):
                    resub = False
                if denial[0] == "29":
                    resub = False
                if resub:
                    r2 = adjudicated + timedelta(days=int(rng.integers(10, 61)))
                    adj2 = r2 + timedelta(days=int(rng.integers(2, 22)))
                    ok = rng.random() < (0.85 if denial[0] in ("197", "22") else cal.get("claim.resubmit_paid_share"))
                    if denial[0] == "197" and ok:
                        self._auth(d, rng, retro=True)
                    d.versions.append({"version": 2, "freq": "7", "received": r2, "adjudicated": adj2,
                                       "paid_date": adj2 + timedelta(days=int(rng.integers(1, 8))) if ok else None,
                                       "status": "paid" if ok else "denied",
                                       "denial": None if ok else self._soft_denial(d, rng), "kind": "corrected"})
                    d.final_paid = ok
            else:
                d.final_paid = True
                v1["paid_date"] = adjudicated + timedelta(days=int(rng.integers(1, 8)))
                r = rng.random()
                if r < cal.get("claim.adjust_share"):
                    r2 = adjudicated + timedelta(days=int(rng.integers(20, 121)))
                    d.versions.append({"version": 2, "freq": "7", "received": r2,
                                       "adjudicated": r2 + timedelta(days=int(rng.integers(2, 15))),
                                       "paid_date": r2 + timedelta(days=int(rng.integers(3, 20))),
                                       "status": "paid", "denial": None, "kind": "adjusted",
                                       "scale": float(rng.choice([0.9, 0.95, 1.05, 1.1]))})
                    v1["status"] = "adjusted"
                elif r < cal.get("claim.adjust_share") + cal.get("claim.reversal_share"):
                    r2 = adjudicated + timedelta(days=int(rng.integers(20, 150)))
                    d.versions.append({"version": 2, "freq": "8", "received": r2,
                                       "adjudicated": r2 + timedelta(days=int(rng.integers(1, 8))),
                                       "paid_date": r2 + timedelta(days=int(rng.integers(2, 12))),
                                       "status": "reversed", "denial": None, "kind": "void"})
                    v1["status"] = "reversed"
                    if rng.random() < 0.5:
                        r3 = r2 + timedelta(days=int(rng.integers(5, 40)))
                        d.versions.append({"version": 3, "freq": "1", "received": r3,
                                           "adjudicated": r3 + timedelta(days=int(rng.integers(2, 15))),
                                           "paid_date": r3 + timedelta(days=int(rng.integers(3, 18))),
                                           "status": "paid", "denial": None, "kind": "rebill"})
                    else:
                        d.final_paid = False

    def _auth(self, d: Draft, rng: np.random.Generator, retro: bool) -> None:
        lead = next((l for l in d.lines if l.svc.pa), d.lines[0])
        sys_, code = code_for(lead.svc, self.lic.cpt)
        if d.facility_type == "inpatient":
            sys_, code = "DRG", d.drg or ""
        requested = d.frm - timedelta(days=int(rng.integers(3, 31)))
        if retro:
            requested = d.to + timedelta(days=int(rng.integers(5, 40)))
        decided = requested + timedelta(days=int(rng.integers(1, 6)))
        self.auths.append(Auth(
            f"PA{len(self.auths) + 1:09d}", d.member.idx, d.plan.plan_id, lead.svc.key, code, requested,
            decided, "approved-retro" if retro else "approved", d.frm - timedelta(days=7),
            d.to + timedelta(days=30), lead.units, d.n, retro,
        ))

    # ---- 4. cost sharing ------------------------------------------------------------------------
    def cost_share(self) -> None:
        self.ind: dict[tuple[int, int, str], Accum] = {}
        self.fam: dict[tuple[int, int, str, str], Accum] = {}
        order = sorted(self.drafts, key=lambda d: (d.frm, d.n))
        for d in order:
            plan = d.plan
            rng = np.random.default_rng([self.seed, 59, d.n])
            year = d.frm.year
            for ln in d.lines:
                ln.allowed = ln.base
            if d.versions[0]["status"] == "denied" and not d.final_paid:
                continue
            # a claim that is paid and later voided without a re-bill nets to zero: price it against
            # a scratch copy so it never moves the real accumulators
            commit = d.final_paid
            ka = (d.member.idx, year, plan.plan_id)
            kf = (d.member.household, year, plan.lob, plan.plan_id)
            a = self.ind.setdefault(ka, Accum())
            f = self.fam.setdefault(kf, Accum())
            if not commit:
                a, f = Accum(a.ded, a.oop), Accum(f.ded, f.oop)
            copay_used: set[str] = set()
            days_left = min(5, d.inpatient_days)
            for ln in d.lines:
                cat = _category(d, ln)
                allowed = ln.allowed
                copay, ded, coins = _share(plan, cat, allowed, a, f, copay_used, d, days_left)
                total = copay + ded + coins
                room = min(_cents(plan.oop_max) - a.oop, _cents(plan.oop_max * plan.family_multiple) - f.oop)
                room = max(0, room)
                if total > room:
                    over = total - room
                    cut = min(coins, over)
                    coins -= cut
                    over -= cut
                    cut = min(ded, over)
                    ded -= cut
                    over -= cut
                    copay -= min(copay, over)
                total = copay + ded + coins
                a.ded += ded
                f.ded += ded
                a.oop += total
                f.oop += total
                paid = allowed - total
                cob = 0
                if d.member.cob and not d.held_for_cob and paid > 0 and rng.random() < 0.5:
                    cob = int(paid * 0.5)
                    paid -= cob
                ln.copay, ln.ded, ln.coins, ln.paid, ln.cob = copay, ded, coins, paid, cob

    # ---- driver ------------------------------------------------------------------------------------
    def run(self) -> None:
        self.build()
        self._price_all()
        self.lifecycle()
        self.cost_share()


def _allowed_pos(lines: list[Line]) -> tuple[str, ...]:
    pos = set(lines[0].svc.pos)
    for ln in lines[1:]:
        pos &= set(ln.svc.pos)
    return tuple(sorted(pos, key=lambda p: ("11", "22", "19", "81", "23", "21").index(p) if p in ("11", "22", "19", "81", "23", "21") else 9))


def _category(d: Draft, ln: Line) -> str:
    s = ln.svc
    primary = d.dx[0][0]
    if s.kind == "preventive" or primary.startswith(PREVENTIVE_DX):
        return "preventive"
    if d.facility_type == "inpatient" or d.enc.setting == "inpatient":
        return "inpatient"
    if s.key.startswith(("EM_ED", "ED_FACILITY")):
        return "ed"
    if s.key == "EM_URGENT_3":
        return "urgent"
    if s.key.startswith(("EM_OFFICE", "EM_TELE", "EM_PREV")) or s.kind == "behavioral" or s.key in (
        "LOPS_EVAL", "LOPS_FOLLOWUP", "EYE_EXAM_DILATED", "DM_SELF_MGMT"
    ):
        spec = d.rendering.specialty if d.rendering else ""
        return "office_pcp" if spec in PRIMARY_CARE or s.kind == "behavioral" else "office_spec"
    if s.kind == "lab":
        return "lab"
    return "other"


def _share(plan: Plan, cat: str, allowed: int, a: Accum, f: Accum, copay_used: set[str],
           d: Draft, days_left: int) -> tuple[int, int, int]:
    if plan.lob == "medicaid" or cat == "preventive":
        return 0, 0, 0
    copay = {"office_pcp": plan.copay_pcp, "office_spec": plan.copay_specialist, "urgent": plan.copay_urgent,
             "ed": plan.copay_ed}.get(cat, 0.0)
    product = plan.product
    if cat in ("office_pcp", "office_spec", "urgent", "ed") and (copay > 0 or product in ("MA-HMO", "MA-PPO", "HMO")):
        if cat in copay_used:
            return 0, 0, 0
        copay_used.add(cat)
        if cat == "ed" and d.enc.parent_stay is not None:
            return 0, 0, 0  # waived on admission
        return min(_cents(copay), allowed), 0, 0
    if cat == "inpatient" and product in ("HMO", "MA-HMO", "MA-PPO"):
        if d.ctype != "I" or "inpt" in copay_used:
            return 0, 0, 0
        copay_used.add("inpt")
        return min(_cents(plan.inpatient_copay_day) * days_left, allowed), 0, 0
    if cat == "lab" and product in ("HMO", "MA-HMO", "MA-PPO"):
        return 0, 0, 0
    rate = plan.coinsurance + (0.2 if d.oon and product in ("PPO", "HDHP") else 0.0)
    if product in ("HMO", "MA-HMO", "MA-PPO"):
        return 0, 0, int(round(allowed * rate))
    ded_room = max(0, min(_cents(plan.deductible) - a.ded, _cents(plan.deductible * plan.family_multiple) - f.ded))
    ded = min(allowed, ded_room)
    coins = int(round((allowed - ded) * rate))
    return 0, ded, coins


def region_stats(values: list[float]) -> float:
    arr = np.asarray(values, dtype=float)
    return float(arr.std() / arr.mean()) if len(arr) > 1 and arr.mean() else math.nan
