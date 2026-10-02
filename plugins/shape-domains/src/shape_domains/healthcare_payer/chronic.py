"""Chronic condition modules: diabetes, hypertension, lipid disorders, CKD, asthma and COPD,
behavioural health, cardiac disease and the simple one-drug conditions.

Each module is a small state machine on the virtual clock.  Review visits renew with exponential
gaps; labs and screening exams fall due by elapsed time; drugs are started and stopped as courses
(``Therapy``) and the pharmacy claims are derived from them later.  No module writes a claim.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .clinical import (
    due,
    ed_visit,
    level_for,
    pcp_specialty,
    simple_service,
    visit,
)
from .engine import SimContext
from .model import Person, Svc
from .problem import ckd_codes, dm_codes, dm_status_codes, htn_code, problem_codes


def _pick(person: Person, options: list[tuple[str, float]]) -> str | None:
    """One option by its probability (probabilities sum to at most 1; the rest is None)."""
    r = person.rng.random()
    acc = 0.0
    for key, p in options:
        acc += p
        if r < acc:
            return key
    return None


def _days(person: Person, p90: float = 0.30) -> int:
    return 90 if person.rng.random() < p90 else 30


def _begin(ctx: SimContext, person: Person, day: date, incident: bool) -> date:
    """Therapy start: a past date for an ongoing course, the event day for a new one."""
    return day if incident else ctx.start - timedelta(days=int(person.rng.integers(20, 700)))


def _start_drug(
    ctx: SimContext,
    person: Person,
    day: date,
    incident: bool,
    drug: str,
    indication: str,
    dx: str,
    spec: str,
    enc: Any = None,
    acute: bool = False,
) -> None:
    d = _begin(ctx, person, day, incident)
    days = _days(person)
    ctx.prescribe(
        person,
        d,
        drug,
        indication,
        dx,
        spec,
        days_supply=days,
        refills=3 if days == 90 else 11,
        acute=acute,
        encounter=enc,
    )


class ChronicBase:
    name = ""
    keys: tuple[str, ...] = ()

    def start(self, ctx: SimContext, person: Person) -> None:
        for key in self.keys:
            if person.has(key):
                self.onboard(ctx, person, ctx.start, False, key)

    def onboard(self, ctx: SimContext, person: Person, day: date, incident: bool, key: str) -> None:
        raise NotImplementedError

    _suppress = False

    def handle(
        self, ctx: SimContext, person: Person, day: date, kind: str, payload: dict[str, Any]
    ) -> None:
        args = dict(payload)
        self._suppress = bool(args.pop("nochain", False))
        try:
            getattr(self, f"on_{kind}")(ctx, person, day, **args)
        finally:
            self._suppress = False

    def _next_review(
        self, ctx: SimContext, person: Person, after: date, per_year: float, key: str
    ) -> None:
        if self._suppress:
            return  # a one-off review in a coverage span: the review chain already runs
        nxt = ctx.exp_next(person, 365.0 / max(per_year, 0.2), after)
        ctx.schedule(person, nxt, self.name, "review", key=key)

    def _first_review(self, ctx: SimContext, person: Person, key: str, chain: bool = True) -> None:
        """A review soon after the window opens and after each (re-)enrolment, inside the coverage
        span: a member who enrols with a known chronic condition establishes care, so the condition
        is coded on a claim.  Only the first starts the renewing review chain (when ``chain``)."""
        lo = max(ctx.start, person.conds[key].onset)
        first = chain
        for s in person.member.spans:
            begin = max(s.start, lo)
            stop = min(s.end or ctx.end, ctx.end)
            if begin > stop:
                continue
            hi = min(begin + timedelta(days=120), stop)
            ctx.schedule(
                person,
                ctx.rand_day(person, begin, hi, weekday_bias=False),
                self.name,
                "review",
                key=key,
                **({} if first else {"nochain": True}),
            )
            first = False

    def _problems(self, person: Person, day: date, skip: tuple[str, ...] = ()) -> list[str]:
        return [c for c in problem_codes(person, day) if c not in skip]


# ---------------------------------------------------------------------------------------------
class DiabetesModule(ChronicBase):
    name = "diabetes"
    keys = ("dm",)

    def onboard(self, ctx: SimContext, person: Person, day: date, incident: bool, key: str) -> None:
        c = person.conds["dm"]
        t1 = c.data["type"] == "E10"
        cal = ctx.cal
        rng = person.rng
        ind = "dm1" if t1 else "dm2"
        spec = "endocrinology" if t1 else pcp_specialty(ctx, person)
        if incident:
            codes = dm_codes(person)
            enc = visit(
                ctx,
                person,
                day,
                self.name,
                "new diabetes diagnosis",
                codes[:1],
                new=True,
                level=4,
                specialty=pcp_specialty(ctx, person),
                labs=("LAB_HBA1C", "LAB_CMP", "LAB_LIPID_PANEL", "LAB_UACR"),
                extra=self._problems(person, day),
            )
            first = day + timedelta(days=int(rng.integers(0, 10)))
            if t1:
                _start_drug(
                    ctx,
                    person,
                    first,
                    True,
                    "insulin_glargine",
                    "dm1",
                    codes[0],
                    "endocrinology",
                    enc,
                )
                _start_drug(
                    ctx,
                    person,
                    first,
                    True,
                    "insulin_lispro",
                    "dm1",
                    codes[0],
                    "endocrinology",
                    enc,
                )
            elif rng.random() < 0.85:
                _start_drug(ctx, person, first, True, "metformin", "dm2", codes[0], spec, enc)
            if rng.random() < 0.35:
                simple_service(
                    ctx,
                    person,
                    first + timedelta(days=14),
                    self.name,
                    "diabetes self-management training",
                    codes[:1],
                    "family_medicine",
                    ("DM_SELF_MGMT",),
                    units=2,
                )
            if rng.random() < 0.5:
                self._eye_exam(ctx, person, first + timedelta(days=int(rng.integers(20, 90))))
        else:
            self._prevalent_drugs(ctx, person, t1, ind, spec)
        if incident:
            self._next_review(ctx, person, day, cal.get("dm.visits_per_year"), "dm")
            self._first_review(ctx, person, "dm", chain=False)
        else:
            self._first_review(ctx, person, "dm")
        for lo, hi in ctx.year_ends():
            if hi < max(ctx.start, c.onset):
                continue
            lo = max(lo, day if incident else lo)
            if lo > hi:
                continue
            if rng.random() < cal.get("dm.eye_exam_annual"):
                ctx.schedule(person, ctx.rand_day(person, lo, hi), self.name, "eye")
            if rng.random() < cal.get("dm.foot_exam_annual"):
                ctx.schedule(person, ctx.rand_day(person, lo, hi), self.name, "foot")
            if rng.random() < cal.get("dm.uacr_annual"):
                ctx.schedule(person, ctx.rand_day(person, lo, hi), self.name, "uacr")
            on_insulin = t1 or any(t.drug_key.startswith("insulin") for t in person.therapies)
            for q in range(4):
                if rng.random() < (0.6 if on_insulin else 0.12):
                    qlo = lo + timedelta(days=q * 90)
                    ctx.schedule(
                        person,
                        ctx.rand_day(person, qlo, qlo + timedelta(days=89)),
                        self.name,
                        "supplies",
                    )
        rate = cal.get("dm.complication_hazard")
        t = rng.exponential(1.0 / rate)
        if t < (ctx.end - day).days / 365.25:
            ctx.schedule(
                person, day + timedelta(days=int(t * 365.25) + 30), self.name, "complication"
            )
        lo = max(ctx.start, day)
        for d in ctx.poisson_days(person, 0.02 if not t1 else 0.06, lo, ctx.end):
            if t1 or any(x.drug_key.startswith("insulin") for x in person.therapies):
                ctx.schedule(person, d, self.name, "acute")
        if t1:
            for d in ctx.poisson_days(person, 0.03, lo, ctx.end):
                ctx.schedule(person, d, self.name, "dka")

    def _prevalent_drugs(
        self, ctx: SimContext, person: Person, t1: bool, ind: str, spec: str
    ) -> None:
        rng = person.rng
        cal = ctx.cal
        dx = dm_codes(person)[0]
        if t1:
            _start_drug(ctx, person, ctx.start, False, "insulin_glargine", ind, dx, "endocrinology")
            _start_drug(
                ctx,
                person,
                ctx.start,
                False,
                "insulin_lispro" if rng.random() < 0.65 else "insulin_aspart",
                ind,
                dx,
                "endocrinology",
            )
            return
        if rng.random() < cal.get("dm.metformin_share"):
            _start_drug(
                ctx,
                person,
                ctx.start,
                False,
                "metformin" if rng.random() < 0.7 else "metformin_er",
                ind,
                dx,
                spec,
            )
        if rng.random() < 0.18:
            _start_drug(
                ctx,
                person,
                ctx.start,
                False,
                "glipizide" if rng.random() < 0.7 else "glimepiride",
                ind,
                dx,
                spec,
            )
        if rng.random() < cal.get("dm.newer_agent_share"):
            agent = _pick(
                person,
                [
                    ("sitagliptin", 0.2),
                    ("empagliflozin", 0.25),
                    ("dapagliflozin", 0.1),
                    ("semaglutide", 0.2),
                    ("dulaglutide", 0.15),
                    ("tirzepatide", 0.1),
                ],
            )
            if agent:
                _start_drug(ctx, person, ctx.start, False, agent, ind, dx, spec)
        if rng.random() < cal.get("dm.type2_on_insulin"):
            _start_drug(ctx, person, ctx.start, False, "insulin_glargine", ind, dx, spec)
            if rng.random() < 0.5:
                _start_drug(ctx, person, ctx.start, False, "insulin_lispro", ind, dx, spec)
        age = person.age(ctx.start)
        if 40 <= age <= 75 and rng.random() < cal.get("dm.statin_age_40_75"):
            self._statin(ctx, person, ctx.start, False, dx)

    def _statin(self, ctx: SimContext, person: Person, day: date, incident: bool, dx: str) -> None:
        if person.has("lipid") or person.has("cad"):
            return  # the lipid or cardiac module owns the statin
        statin = _pick(
            person,
            [
                ("atorvastatin", 0.55),
                ("rosuvastatin", 0.25),
                ("simvastatin", 0.10),
                ("pravastatin", 0.10),
            ],
        )
        if statin:
            _start_drug(
                ctx, person, day, incident, statin, "dm2_statin", dx, pcp_specialty(ctx, person)
            )

    # ---- events --------------------------------------------------------------------------------
    def on_review(self, ctx: SimContext, person: Person, day: date, key: str) -> None:
        if not person.has("dm"):
            return
        t1 = person.conds["dm"].data["type"] == "E10"
        rng = person.rng
        spec = (
            "endocrinology" if rng.random() < (0.7 if t1 else 0.12) else pcp_specialty(ctx, person)
        )
        codes = dm_codes(person)
        labs = tuple(
            k
            for k, days, p in (
                ("LAB_HBA1C", 75, 0.8),
                ("LAB_LIPID_PANEL", 330, 0.6),
                ("LAB_CMP", 200, 0.5),
            )
            if due(person, k, day, days, p)
        )
        visit(
            ctx,
            person,
            day,
            self.name,
            "diabetes review",
            codes[:1],
            specialty=spec,
            level=level_for(person, 0.1),
            labs=labs,
            extra=codes[1:] + dm_status_codes(person) + self._problems(person, day, tuple(codes)),
        )
        # therapy changes: intensify with a newer agent or insulin
        if not t1 and rng.random() < 0.06:
            agent = _pick(
                person,
                [
                    ("empagliflozin", 0.3),
                    ("semaglutide", 0.25),
                    ("dulaglutide", 0.15),
                    ("tirzepatide", 0.15),
                    ("insulin_glargine", 0.15),
                ],
            )
            if agent:
                _start_drug(ctx, person, day, True, agent, "dm2", codes[0], spec)
        self._next_review(ctx, person, day, ctx.cal.get("dm.visits_per_year"), "dm")

    def on_eye(self, ctx: SimContext, person: Person, day: date) -> None:
        if person.has("dm"):
            self._eye_exam(ctx, person, day)

    def _eye_exam(self, ctx: SimContext, person: Person, day: date) -> None:
        comp = person.conds["dm"].data["complications"]
        dx = (
            ["E11.319"]
            if "retinopathy" in comp and person.conds["dm"].data["type"] == "E11"
            else dm_codes(person)[:1]
        )
        spec = "ophthalmology" if person.rng.random() < 0.45 else "optometry"
        simple_service(
            ctx, person, day, self.name, "dilated retinal eye exam", dx, spec, ("EYE_EXAM_DILATED",)
        )

    def on_foot(self, ctx: SimContext, person: Person, day: date) -> None:
        if not person.has("dm"):
            return
        comp = person.conds["dm"].data["complications"]
        key = (
            "LOPS_EVAL"
            if "neuropathy" in comp and not person.last_done.get("LOPS_EVAL")
            else "LOPS_FOLLOWUP"
        )
        person.last_done[key] = day
        simple_service(
            ctx,
            person,
            day,
            self.name,
            "diabetic foot exam",
            dm_codes(person)[:2],
            "podiatry",
            (key,),
        )

    def on_uacr(self, ctx: SimContext, person: Person, day: date) -> None:
        if person.has("dm"):
            simple_service(
                ctx,
                person,
                day,
                self.name,
                "urine albumin-creatinine ratio",
                dm_codes(person)[:1],
                "laboratory",
                ("LAB_UACR",),
                setting="lab",
            )

    def on_supplies(self, ctx: SimContext, person: Person, day: date) -> None:
        if person.has("dm"):
            simple_service(
                ctx,
                person,
                day,
                self.name,
                "glucose testing supplies",
                dm_codes(person)[:1] + dm_status_codes(person),
                "dme_supplier",
                ("DME_GLUCOSE_STRIPS", "DME_LANCETS"),
                setting="dme",
            )

    def on_complication(self, ctx: SimContext, person: Person, day: date) -> None:
        if not person.has("dm"):
            return
        data = person.conds["dm"].data
        options = [
            k
            for k in ("neuropathy", "retinopathy", "angiopathy", "ulcer")
            if k not in data["complications"]
        ]
        if options:
            data["complications"].append(options[int(person.rng.integers(0, len(options)))])
            data["hyperglycemia"] = data.get("hyperglycemia") or bool(person.rng.random() < 0.4)

    def on_acute(self, ctx: SimContext, person: Person, day: date) -> None:
        t1 = person.has("dm") and person.conds["dm"].data["type"] == "E10"
        ed_visit(
            ctx,
            person,
            day,
            self.name,
            "hypoglycemia",
            ["E10.649" if t1 else "E11.649"],
            extra=self._problems(person, day),
            level=4,
            labs=("LAB_BMP",),
            ambulance=0.5,
        )

    def on_dka(self, ctx: SimContext, person: Person, day: date) -> None:
        ed_visit(
            ctx,
            person,
            day,
            self.name,
            "diabetic ketoacidosis",
            ["E10.10"],
            level=5,
            extra=self._problems(person, day, ("E10.10",)),
            admit={"principal": ["E10.10"], "secondary": self._problems(person, day, ("E10.10",))},
            ambulance=0.4,
        )


# ---------------------------------------------------------------------------------------------
class HypertensionModule(ChronicBase):
    name = "htn"
    keys = ("htn",)

    def onboard(self, ctx: SimContext, person: Person, day: date, incident: bool, key: str) -> None:
        rng = person.rng
        spec = pcp_specialty(ctx, person)
        if incident:
            visit(
                ctx,
                person,
                day,
                self.name,
                "new hypertension diagnosis",
                ["I10"],
                new=True,
                level=3,
                labs=("LAB_BMP", "LAB_LIPID_PANEL"),
                extra=self._problems(person, day),
            )
        if (incident and rng.random() < 0.85) or (
            not incident and rng.random() < ctx.cal.get("htn.treated")
        ):
            self._regimen(ctx, person, day, incident, spec)
        if incident:
            self._next_review(ctx, person, day, ctx.cal.get("htn.visits_per_year"), "htn")
            self._first_review(ctx, person, "htn", chain=False)
        else:
            self._first_review(ctx, person, "htn")

    def _regimen(
        self, ctx: SimContext, person: Person, day: date, incident: bool, spec: str
    ) -> None:
        rng = person.rng
        dx = htn_code(person)
        raas = ("lisinopril", "losartan")
        agents: list[str] = []
        if person.has("hf"):
            agents.append("carvedilol" if rng.random() < 0.6 else "metoprolol_succ")
            agents.append(raas[int(rng.integers(0, 2))])
        elif person.has("dm") or person.has("ckd"):
            agents.append(raas[int(rng.integers(0, 2))])
        else:
            agents.append(
                _pick(
                    person,
                    [
                        ("lisinopril", 0.30),
                        ("losartan", 0.20),
                        ("amlodipine", 0.30),
                        ("hydrochlorothiazide", 0.12),
                        ("chlorthalidone", 0.08),
                    ],
                )
                or "lisinopril"
            )
        if rng.random() < 0.35:
            agents.append(
                _pick(
                    person,
                    [("amlodipine", 0.5), ("hydrochlorothiazide", 0.3), ("chlorthalidone", 0.1)],
                )
                or "amlodipine"
            )
        if rng.random() < 0.12:
            agents.append("metoprolol_succ" if rng.random() < 0.6 else "spironolactone")
        for a in dict.fromkeys(agents):
            _start_drug(ctx, person, day, incident, a, "htn", dx, spec)

    def on_review(self, ctx: SimContext, person: Person, day: date, key: str) -> None:
        if not person.has("htn"):
            return
        spec = "cardiology" if person.rng.random() < 0.08 else pcp_specialty(ctx, person)
        labs = ("LAB_BMP",) if due(person, "LAB_BMP", day, 200, 0.5) else ()
        visit(
            ctx,
            person,
            day,
            self.name,
            "hypertension review",
            [htn_code(person)],
            specialty=spec,
            level=level_for(person, 0.05),
            labs=labs,
            extra=self._problems(person, day, (htn_code(person),)),
        )
        self._next_review(ctx, person, day, ctx.cal.get("htn.visits_per_year"), "htn")


# ---------------------------------------------------------------------------------------------
class LipidModule(ChronicBase):
    name = "lipid"
    keys = ("lipid",)

    def onboard(self, ctx: SimContext, person: Person, day: date, incident: bool, key: str) -> None:
        rng = person.rng
        code = person.conds["lipid"].code
        spec = pcp_specialty(ctx, person)
        if incident:
            visit(
                ctx,
                person,
                day,
                self.name,
                "new lipid disorder",
                [code],
                new=False,
                level=3,
                labs=("LAB_LIPID_PANEL",),
                extra=self._problems(person, day),
            )
        if rng.random() < (0.6 if incident else ctx.cal.get("lipid.statin_treated")):
            statin = (
                _pick(
                    person,
                    [
                        ("atorvastatin", 0.5),
                        ("rosuvastatin", 0.25),
                        ("simvastatin", 0.12),
                        ("pravastatin", 0.13),
                    ],
                )
                or "atorvastatin"
            )
            _start_drug(ctx, person, day, incident, statin, "lipid", code, spec)
            if rng.random() < 0.10:
                _start_drug(ctx, person, day, incident, "ezetimibe", "lipid", code, spec)
        if code == "E78.2" and rng.random() < 0.10:
            _start_drug(ctx, person, day, incident, "fenofibrate", "lipid", code, spec)
        if incident:
            ctx.schedule(
                person,
                day + timedelta(days=int(rng.integers(60, 120))),
                self.name,
                "review",
                key="lipid",
            )
            self._first_review(ctx, person, "lipid", chain=False)
        else:
            self._first_review(ctx, person, "lipid")

    def on_review(self, ctx: SimContext, person: Person, day: date, key: str) -> None:
        if not person.has("lipid"):
            return
        labs = (
            ("LAB_LIPID_PANEL",)
            if due(person, "LAB_LIPID_PANEL", day, 300, ctx.cal.get("lipid.panel_per_year"))
            else ()
        )
        visit(
            ctx,
            person,
            day,
            self.name,
            "lipid follow-up",
            [person.conds["lipid"].code],
            level=3,
            labs=labs,
            extra=self._problems(person, day),
        )
        self._next_review(ctx, person, day, 1.2, "lipid")


# ---------------------------------------------------------------------------------------------
class CkdModule(ChronicBase):
    name = "ckd"
    keys = ("ckd",)

    def onboard(self, ctx: SimContext, person: Person, day: date, incident: bool, key: str) -> None:
        c = person.conds["ckd"]
        rng = person.rng
        if incident:
            visit(
                ctx,
                person,
                day,
                self.name,
                "new chronic kidney disease",
                ckd_codes(person)[:1],
                level=4,
                labs=("LAB_CMP", "LAB_UACR", "LAB_CBC"),
                extra=self._problems(person, day),
            )
        if c.stage >= 4 and rng.random() < 0.3:
            _start_drug(
                ctx, person, day, incident, "furosemide", "ckd", ckd_codes(person)[0], "nephrology"
            )
        if person.has("dm") and rng.random() < 0.15:
            _start_drug(
                ctx,
                person,
                day,
                incident,
                "empagliflozin",
                "ckd",
                ckd_codes(person)[0],
                "nephrology",
            )
        if person.has("dm") and not person.has("htn") and rng.random() < 0.5:
            _start_drug(
                ctx,
                person,
                day,
                incident,
                "lisinopril",
                "dm_ckd",
                ckd_codes(person)[0],
                pcp_specialty(ctx, person),
            )
        if incident:
            self._next_review(ctx, person, day, 1.0, "ckd")
            self._first_review(ctx, person, "ckd", chain=False)
        else:
            self._first_review(ctx, person, "ckd")
        h = ctx.cal.get("ckd.progress_hazard")[str(min(c.stage, 5))] if c.stage < 6 else 0.0
        if h:
            t = rng.exponential(1.0 / h)
            if t < (ctx.end - day).days / 365.25:
                ctx.schedule(
                    person, day + timedelta(days=int(t * 365.25) + 30), self.name, "progress"
                )
        if c.stage >= 4:
            for d in ctx.poisson_days(person, 0.15, max(ctx.start, day), ctx.end):
                ctx.schedule(person, d, self.name, "admit")
        if c.stage == 6:
            self._begin_dialysis(ctx, person, max(ctx.start, day))

    def _begin_dialysis(self, ctx: SimContext, person: Person, day: date) -> None:
        person.flags["dialysis"] = True
        cur = date(day.year, day.month, 1)
        while cur <= ctx.end:
            nxt = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
            ctx.schedule(person, min(nxt - timedelta(days=1), ctx.end), self.name, "dialysis_month")
            cur = nxt
        death = day + timedelta(days=int(person.rng.exponential(5.5 * 365)))
        if death <= ctx.end:
            ctx.kill(person, death)
            ctx.schedule(
                person, death - timedelta(days=int(person.rng.integers(1, 6))), "events", "terminal"
            )

    def on_review(self, ctx: SimContext, person: Person, day: date, key: str) -> None:
        if not person.has("ckd"):
            return
        c = person.conds["ckd"]
        spec = (
            "nephrology"
            if c.stage >= 3 and person.rng.random() < 0.6
            else pcp_specialty(ctx, person)
        )
        labs = tuple(
            k
            for k, d, p in (
                ("LAB_CMP", 120, 0.7),
                ("LAB_UACR", 330, 0.5),
                ("LAB_CBC", 150, 0.5 if c.stage >= 4 else 0.1),
            )
            if due(person, k, day, d, p)
        )
        extra = self._problems(person, day, tuple(ckd_codes(person)))
        if c.stage >= 4 and person.rng.random() < 0.5 and "D63.1" not in extra:
            extra.insert(0, "D63.1")
        visit(
            ctx,
            person,
            day,
            self.name,
            "chronic kidney disease review",
            ckd_codes(person)[:1],
            specialty=spec,
            level=4,
            labs=labs,
            extra=ckd_codes(person)[1:] + extra,
        )
        per_year = ctx.cal.get("ckd.nephrology_visits") if c.stage >= 3 else 1.0
        self._next_review(ctx, person, day, per_year, "ckd")

    def on_progress(self, ctx: SimContext, person: Person, day: date) -> None:
        if not person.has("ckd") or person.conds["ckd"].stage >= 6:
            return
        c = person.conds["ckd"]
        c.stage += 1
        c.data["sub"] = ["a", "b", ""][int(person.rng.integers(0, 3))] if c.stage == 3 else ""
        h = ctx.cal.get("ckd.progress_hazard")
        if c.stage == 6:
            self._begin_dialysis(ctx, person, day)
            return
        rate = h[str(c.stage)]
        t = person.rng.exponential(1.0 / rate)
        if t < (ctx.end - day).days / 365.25:
            ctx.schedule(person, day + timedelta(days=int(t * 365.25) + 30), self.name, "progress")
        if c.stage >= 4:
            for d in ctx.poisson_days(person, 0.15, day, ctx.end):
                ctx.schedule(person, d, self.name, "admit")

    def on_admit(self, ctx: SimContext, person: Person, day: date) -> None:
        if person.has("ckd") and person.conds["ckd"].stage >= 4:
            ed_visit(
                ctx,
                person,
                day,
                self.name,
                "acute kidney injury on CKD",
                ["N17.9"],
                level=5,
                admit={
                    "principal": ["N17.9"],
                    "secondary": self._problems(person, day, ("N17.9",)),
                },
                labs=("LAB_BMP",),
                ambulance=0.2,
            )

    def on_dialysis_month(self, ctx: SimContext, person: Person, day: date) -> None:
        if not person.flags.get("dialysis"):
            return
        dx = ctx.dx_list(person, day, ["N18.6", "Z99.2"], self._problems(person, day), 0.8)
        enc = ctx.encounter(
            person,
            day,
            "dialysis",
            "dialysis_center",
            dx,
            [Svc("DIALYSIS_SESSION", units=13), Svc("DIALYSIS_MD_MONTH", specialty="nephrology")],
            self.name,
            "hemodialysis, monthly",
            admit=date(day.year, day.month, 1),
            discharge=day,
        )
        if enc is not None:
            enc.facility = ctx.directory.pick(person, "dialysis_center", day)


# ---------------------------------------------------------------------------------------------
class RespChronicModule(ChronicBase):
    name = "resp_chronic"
    keys = ("asthma", "copd")
    _EXAC = {"J45.909": "J45.901", "J45.20": "J45.21", "J45.30": "J45.31", "J45.40": "J45.41"}

    def onboard(self, ctx: SimContext, person: Person, day: date, incident: bool, key: str) -> None:
        rng = person.rng
        c = person.conds[key]
        spec = pcp_specialty(ctx, person)
        dx = c.code if key == "asthma" else "J44.9"
        if incident:
            visit(
                ctx,
                person,
                day,
                self.name,
                f"new {key}",
                [dx],
                new=True,
                level=4,
                extra=self._problems(person, day),
                imaging=("IMG_CHEST_XRAY",) if key == "copd" else (),
            )
        if key == "asthma":
            if dx in ("J45.30", "J45.40") and rng.random() < 0.7:
                _start_drug(
                    ctx,
                    person,
                    day,
                    incident,
                    _pick(person, [("fluticasone_salmeterol", 0.5), ("budesonide_formoterol", 0.2)])
                    or "montelukast",
                    "asthma",
                    dx,
                    spec,
                )
            elif dx == "J45.909" and rng.random() < 0.3:
                _start_drug(
                    ctx, person, day, incident, "fluticasone_salmeterol", "asthma", dx, spec
                )
            if rng.random() < 0.25:
                _start_drug(ctx, person, day, incident, "montelukast", "asthma", dx, spec)
            rate = ctx.cal.get("asthma.exac_rate") * (
                1.4 if dx in ("J45.40",) else 0.8 if dx == "J45.20" else 1.0
            )
        else:
            if rng.random() < 0.55:
                _start_drug(ctx, person, day, incident, "tiotropium", "copd", dx, "pulmonology")
            if rng.random() < 0.45:
                _start_drug(
                    ctx, person, day, incident, "fluticasone_salmeterol", "copd", dx, "pulmonology"
                )
            rate = ctx.cal.get("copd.exac_rate")
        # a rescue inhaler about once a year
        if rng.random() < 0.9:
            ctx.schedule(
                person,
                ctx.rand_day(
                    person, max(ctx.start, day), max(ctx.start, day) + timedelta(days=240)
                ),
                self.name,
                "rescue",
                key=key,
            )
        for d in ctx.seasonal_dates(
            person, rate, ctx.cal.get("resp.season_weight"), max(ctx.start, day), ctx.end
        ):
            ctx.schedule(person, d, self.name, "exac", key=key)
        if incident:
            self._next_review(ctx, person, day, 1.5, key)
            self._first_review(ctx, person, key, chain=False)
        else:
            self._first_review(ctx, person, key)

    def on_review(self, ctx: SimContext, person: Person, day: date, key: str) -> None:
        if not person.has(key):
            return
        c = person.conds[key]
        pulm = person.rng.random() < (0.6 if key == "copd" else 0.15)
        spec = "pulmonology" if pulm else pcp_specialty(ctx, person)
        dx = c.code if key == "asthma" else "J44.9"
        svc = ("SPIROMETRY",) if pulm and due(person, "SPIROMETRY", day, 330, 0.5) else ()
        if svc:
            person.last_done["SPIROMETRY"] = day
        visit(
            ctx,
            person,
            day,
            self.name,
            f"{key} review",
            [dx],
            specialty=spec,
            level=level_for(person, 0.1),
            extra_services=svc,
            extra=self._problems(person, day, (dx,)),
        )
        self._next_review(ctx, person, day, 2.0 if key == "copd" else 1.4, key)

    def on_rescue(self, ctx: SimContext, person: Person, day: date, key: str) -> None:
        if person.has(key):
            ctx.prescribe(
                person,
                day,
                "albuterol_hfa",
                key,
                person.conds[key].code or "J44.9",
                pcp_specialty(ctx, person),
                days_supply=25,
                refills=0,
                acute=True,
                quantity=1,
            )
            nxt = day + timedelta(days=int(person.rng.exponential(330)) + 120)
            ctx.schedule(person, nxt, self.name, "rescue", key=key)

    def on_exac(self, ctx: SimContext, person: Person, day: date, key: str) -> None:
        if not person.has(key):
            return
        rng = person.rng
        c = person.conds[key]
        spec = pcp_specialty(ctx, person)
        if key == "asthma":
            dx = self._EXAC.get(c.code, "J45.901")
            ed = rng.random() < ctx.cal.get("asthma.exac_ed_share")
            admit = (
                {"principal": [dx], "secondary": self._problems(person, day, (dx, c.code))}
                if ed and rng.random() < 0.06
                else None
            )
            if ed:
                ed_visit(
                    ctx,
                    person,
                    day,
                    self.name,
                    "asthma exacerbation",
                    [dx],
                    level=4,
                    imaging=("IMG_CHEST_XRAY",) if rng.random() < 0.3 else (),
                    admit=admit,
                    ambulance=0.1,
                )
            else:
                visit(
                    ctx,
                    person,
                    day,
                    self.name,
                    "asthma exacerbation",
                    [dx],
                    setting="urgent" if rng.random() < 0.3 else None,
                    specialty=spec,
                    extra=self._problems(person, day, (dx, c.code)),
                )
            if rng.random() < 0.55:
                ctx.prescribe(
                    person,
                    day,
                    "prednisone",
                    "asthma_exac",
                    dx,
                    spec,
                    days_supply=5,
                    refills=0,
                    acute=True,
                    quantity=10,
                )
        else:
            dx = "J44.1"
            if rng.random() < ctx.cal.get("copd.exac_admit_share"):
                ed_visit(
                    ctx,
                    person,
                    day,
                    self.name,
                    "COPD exacerbation",
                    [dx],
                    level=5,
                    extra=self._problems(person, day, (dx, "J44.9")),
                    admit={
                        "principal": [dx],
                        "secondary": self._problems(person, day, (dx, "J44.9")),
                        "pcs": ("5A1935Z",) if rng.random() < 0.06 else (),
                        "complication_p": 0.10,
                    },
                    imaging=("IMG_CHEST_XRAY",),
                    ambulance=0.3,
                )
            else:
                visit(
                    ctx,
                    person,
                    day,
                    self.name,
                    "COPD exacerbation",
                    [dx],
                    specialty=spec,
                    level=4,
                    setting="urgent" if rng.random() < 0.2 else None,
                    extra=self._problems(person, day, (dx, "J44.9")),
                    imaging=("IMG_CHEST_XRAY",) if rng.random() < 0.4 else (),
                )
                if rng.random() < 0.7:
                    ctx.prescribe(
                        person,
                        day,
                        "prednisone",
                        "copd_exac",
                        dx,
                        spec,
                        days_supply=5,
                        refills=0,
                        acute=True,
                        quantity=10,
                    )
                if rng.random() < 0.5:
                    ctx.prescribe(
                        person,
                        day,
                        "azithromycin",
                        "copd_exac",
                        dx,
                        spec,
                        days_supply=5,
                        refills=0,
                        acute=True,
                        quantity=6,
                    )


# ---------------------------------------------------------------------------------------------
class BehavioralModule(ChronicBase):
    name = "behavioral"
    keys = (
        "depression",
        "anxiety",
        "adhd",
        "sud_opioid",
        "sud_alcohol",
        "bipolar",
        "schizophrenia",
    )
    _CODE = {
        "sud_opioid": "F11.20",
        "sud_alcohol": "F10.20",
        "bipolar": "F31.9",
        "schizophrenia": "F20.9",
    }
    _DRUGS: dict[str, list[tuple[str, float]]] = {
        "depression": [
            ("sertraline", 0.35),
            ("escitalopram", 0.25),
            ("fluoxetine", 0.10),
            ("bupropion_xl", 0.12),
            ("trazodone", 0.08),
        ],
        "anxiety": [
            ("sertraline", 0.28),
            ("escitalopram", 0.27),
            ("alprazolam", 0.12),
            ("lorazepam", 0.05),
        ],
        "adhd": [("methylphenidate_er", 0.6), ("lisdexamfetamine", 0.2)],
        "sud_opioid": [("buprenorphine_naloxone", 0.35)],
        "sud_alcohol": [],
        "bipolar": [("quetiapine", 0.45), ("lamotrigine", 0.35), ("aripiprazole", 0.15)],
        "schizophrenia": [("aripiprazole", 0.5), ("quetiapine", 0.45)],
    }

    def _dx(self, person: Person, key: str) -> str:
        return self._CODE.get(key) or person.conds[key].code

    def onboard(self, ctx: SimContext, person: Person, day: date, incident: bool, key: str) -> None:
        rng = person.rng
        dx = self._dx(person, key)
        treated = incident or rng.random() < ctx.cal.get("bh.treated_share")
        if not treated:
            return
        if incident:
            visit(
                ctx,
                person,
                day,
                self.name,
                f"new {key}",
                [dx],
                new=True,
                level=4,
                specialty=pcp_specialty(ctx, person),
                extra=self._problems(person, day, (dx,)),
            )
        spec = (
            "psychiatry"
            if key in ("bipolar", "schizophrenia") or rng.random() < 0.3
            else pcp_specialty(ctx, person)
        )
        drug = _pick(person, self._DRUGS[key])
        if drug:
            _start_drug(
                ctx,
                person,
                day,
                incident,
                drug,
                key if key not in ("depression", "anxiety") else key,
                dx,
                spec,
            )
        if key == "adhd" and rng.random() < 0.0:
            return
        n = ctx.cal.get("bh.visits_per_year")[key]
        if incident:
            self._next_review(ctx, person, day, n, key)
            self._first_review(ctx, person, key, chain=False)
        else:
            self._first_review(ctx, person, key)
        rate = ctx.cal.get("bh.admit_per_year").get(key, 0.0)
        for d in ctx.poisson_days(person, rate, max(ctx.start, day), ctx.end):
            ctx.schedule(person, d, self.name, "admit", key=key)

    def on_review(self, ctx: SimContext, person: Person, day: date, key: str) -> None:
        if not person.has(key):
            return
        rng = person.rng
        dx = self._dx(person, key)
        age = person.age(day)
        if key.startswith("sud") and person.member.lob == "medicaid":
            svc, spec = "SUD_COUNSEL", "psychology"
        elif key in ("bipolar", "schizophrenia") or (rng.random() < 0.25):
            svc, spec = "MED_MGMT_PSYCH", "psychiatry"
        else:
            svc, spec = "PSYCHOTHERAPY_45", "psychology" if rng.random() < 0.65 else "psychiatry"
        if age < 12 and svc == "PSYCHOTHERAPY_45":
            spec = "psychology"
        setting = (
            "telehealth"
            if day >= date(2020, 3, 1) and rng.random() < 0.35 and svc != "SUD_COUNSEL"
            else "office"
        )
        simple_service(
            ctx,
            person,
            day,
            self.name,
            f"{key} visit",
            [dx],
            spec,
            (svc,),
            setting=setting,
            extra=self._problems(person, day, (dx,)),
        )
        self._next_review(ctx, person, day, ctx.cal.get("bh.visits_per_year")[key], key)

    def on_admit(self, ctx: SimContext, person: Person, day: date, key: str) -> None:
        if not person.has(key):
            return
        dx = self._dx(person, key)
        if key in ("depression", "bipolar", "schizophrenia", "anxiety"):
            dx = "F33.2" if key == "depression" else dx
        sec = self._problems(person, day, (dx,))
        ed_visit(
            ctx,
            person,
            day,
            self.name,
            f"{key} crisis",
            ["R45.851"] if key == "depression" and person.rng.random() < 0.5 else [dx],
            extra=[dx] + sec,
            level=4,
            admit={"principal": [dx], "secondary": sec},
            ambulance=0.2,
        )


# ---------------------------------------------------------------------------------------------
class CardiacModule(ChronicBase):
    name = "cardiac"
    keys = ("cad", "hf", "afib")

    def onboard(self, ctx: SimContext, person: Person, day: date, incident: bool, key: str) -> None:
        rng = person.rng
        code = {
            "cad": "I25.10",
            "hf": "I50.22"
            if person.conds.get("hf") and person.conds["hf"].data.get("systolic")
            else "I50.9",
            "afib": "I48.91",
        }[key]
        if incident:
            visit(
                ctx,
                person,
                day,
                self.name,
                f"new {key}",
                [code],
                specialty="cardiology",
                new=True,
                level=4,
                labs=("LAB_BMP",),
                extra=self._problems(person, day),
            )
        if key == "cad":
            if rng.random() < 0.8:
                _start_drug(ctx, person, day, incident, "atorvastatin", "cad", code, "cardiology")
            if rng.random() < 0.6:
                _start_drug(
                    ctx, person, day, incident, "metoprolol_succ", "cad", code, "cardiology"
                )
            if rng.random() < 0.25:
                _start_drug(ctx, person, day, incident, "clopidogrel", "cad", code, "cardiology")
            rate = 0.015
            principal = "I21.4"
        elif key == "hf":
            for drug, p in (
                ("carvedilol", 0.5),
                ("lisinopril", 0.35),
                ("losartan", 0.2),
                ("spironolactone", 0.3),
                ("furosemide", 0.6),
                ("empagliflozin", 0.2),
            ):
                if rng.random() < p:
                    _start_drug(ctx, person, day, incident, drug, "hf", code, "cardiology")
            rate = 0.18 if person.age(day) >= 65 else 0.10
            principal = code
        else:
            if rng.random() < 0.5:
                _start_drug(ctx, person, day, incident, "apixaban", "afib", code, "cardiology")
            if rng.random() < 0.5:
                _start_drug(
                    ctx, person, day, incident, "metoprolol_succ", "afib", code, "cardiology"
                )
            rate = 0.05
            principal = code
        if incident:
            self._next_review(ctx, person, day, 2.5, key)
            self._first_review(ctx, person, key, chain=False)
        else:
            self._first_review(ctx, person, key)
        for d in ctx.poisson_days(person, rate, max(ctx.start, day), ctx.end):
            ctx.schedule(person, d, self.name, "admit", key=key, principal=principal)

    def on_review(self, ctx: SimContext, person: Person, day: date, key: str) -> None:
        if not person.has(key):
            return
        code = {"cad": "I25.10", "hf": "I50.9", "afib": "I48.91"}[key]
        labs = ("LAB_BMP",) if due(person, "LAB_BMP", day, 150, 0.5) else ()
        visit(
            ctx,
            person,
            day,
            self.name,
            f"{key} review",
            [code],
            specialty="cardiology" if person.rng.random() < 0.65 else pcp_specialty(ctx, person),
            level=4,
            labs=labs,
            extra=self._problems(person, day, (code,)),
        )
        self._next_review(ctx, person, day, 2.5, key)

    def on_admit(
        self, ctx: SimContext, person: Person, day: date, key: str, principal: str
    ) -> None:
        if person.has(key):
            sec = self._problems(person, day, (principal,))
            ed_visit(
                ctx,
                person,
                day,
                self.name,
                f"{key} admission",
                [principal],
                level=5,
                extra=sec,
                admit={"principal": [principal], "secondary": sec, "complication_p": 0.08},
                labs=("LAB_BMP", "LAB_CBC"),
                ambulance=0.35,
            )


# ---------------------------------------------------------------------------------------------
class SimpleChronicModule(ChronicBase):
    name = "simple"
    keys = ("hypothyroid", "gerd", "osteoporosis", "bph", "autoimmune")
    _SPEC: dict[str, dict[str, Any]] = {
        "hypothyroid": {
            "dx": "E03.9",
            "drugs": [("levothyroxine", 0.9)],
            "labs": ("LAB_TSH",),
            "per_year": 1.2,
            "spec": None,
        },
        "gerd": {
            "dx": "K21.9",
            "drugs": [("omeprazole", 0.5), ("pantoprazole", 0.3)],
            "labs": (),
            "per_year": 0.8,
            "spec": None,
        },
        "osteoporosis": {
            "dx": "M81.0",
            "drugs": [("alendronate", 0.5)],
            "labs": (),
            "per_year": 1.0,
            "spec": None,
        },
        "bph": {
            "dx": "N40.1",
            "drugs": [("tamsulosin", 0.6)],
            "labs": (),
            "per_year": 1.0,
            "spec": "urology",
        },
        "autoimmune": {
            "dx": None,
            "drugs": [
                ("adalimumab", 0.28),
                ("etanercept", 0.10),
                ("ustekinumab", 0.08),
                ("methotrexate", 0.30),
            ],
            "labs": ("LAB_CBC", "LAB_CMP"),
            "per_year": 3.0,
            "spec": "rheumatology",
        },
    }

    def onboard(self, ctx: SimContext, person: Person, day: date, incident: bool, key: str) -> None:
        spec = self._SPEC[key]
        dx = spec["dx"] or person.conds[key].code
        pcp = pcp_specialty(ctx, person)
        if incident:
            visit(
                ctx,
                person,
                day,
                self.name,
                f"new {key}",
                [dx],
                level=3,
                labs=spec["labs"],
                extra=self._problems(person, day),
            )
        drug = _pick(person, spec["drugs"])
        if drug:
            _start_drug(
                ctx,
                person,
                day,
                incident,
                drug,
                key,
                dx,
                person.conds[key].data.get("specialty", pcp) if key == "autoimmune" else pcp,
            )
        if incident:
            self._next_review(ctx, person, day, spec["per_year"], key)
            self._first_review(ctx, person, key, chain=False)
        else:
            self._first_review(ctx, person, key)

    def on_review(self, ctx: SimContext, person: Person, day: date, key: str) -> None:
        if not person.has(key):
            return
        spec = self._SPEC[key]
        dx = spec["dx"] or person.conds[key].code
        specialty = (
            spec["spec"]
            if spec["spec"] and person.rng.random() < (0.9 if key == "autoimmune" else 0.3)
            else pcp_specialty(ctx, person)
        )
        if key == "autoimmune":
            specialty = (
                person.conds[key].data["specialty"]
                if person.rng.random() < 0.85
                else pcp_specialty(ctx, person)
            )
        labs = tuple(
            k for k in spec["labs"] if due(person, k, day, 120 if key == "autoimmune" else 330, 0.8)
        )
        visit(
            ctx,
            person,
            day,
            self.name,
            f"{key} review",
            [dx],
            specialty=specialty,
            level=level_for(person),
            labs=labs,
            extra=self._problems(person, day, (dx,)),
        )
        self._next_review(ctx, person, day, spec["per_year"], key)


CHRONIC_MODULES = (
    DiabetesModule,
    HypertensionModule,
    LipidModule,
    CkdModule,
    RespChronicModule,
    BehavioralModule,
    CardiacModule,
    SimpleChronicModule,
)
