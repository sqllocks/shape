"""Event modules: pregnancy and maternity, cancer pathways, acute and preventive care, and the
stays that end in readmission or death."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .calibration import band
from .clinical import (
    admit_stay,
    ed_visit,
    pcp_specialty,
    simple_service,
    visit,
)
from .engine import SimContext
from .model import Cond, Person, Svc
from .problem import problem_codes

_BIRTH_KEY = {
    "15-19": "15-19",
    "20-24": "20-24",
    "25-29": "25-29",
    "30-34": "30-34",
    "35-39": "35-39",
    "40-44": "40-44",
}


def _birth_rate(ctx: SimContext, person: Person, day: date) -> float:
    age = person.age(day)
    table = ctx.cal.get("preg.births_per_1000")
    if age < 12 or age > 49:
        rate = 0.0
    elif age < 15:
        rate = 0.3
    elif age < 45:
        rate = table[f"{15 + (age - 15) // 5 * 5}-{19 + (age - 15) // 5 * 5}"]
    else:
        rate = 0.7
    mult = {"commercial": 1.0, "medicaid": 1.35, "ma": 0.3}[person.member.lob]
    return rate / 1000.0 * mult


# ---------------------------------------------------------------------------------------------
_PRETERM_CODE = {
    28: "P07.31",
    29: "P07.32",
    30: "P07.33",
    31: "P07.34",
    32: "P07.35",
    33: "P07.36",
    34: "P07.37",
    35: "P07.38",
    36: "P07.39",
}


class PregnancyModule:
    name = "pregnancy"

    def start(self, ctx: SimContext, person: Person) -> None:
        if person.member.sex != "F":
            return
        lam = _birth_rate(ctx, person, ctx.start)
        if lam <= 0:
            return
        rng = person.rng
        cur = ctx.start - timedelta(days=280)
        mean_gap = max(60.0, (1.0 / lam - 0.93) * 365.0)
        for _ in range(4):
            c = cur + timedelta(days=int(min(rng.exponential(mean_gap), 36500.0)))
            preterm = rng.random() < ctx.cal.get("preg.preterm_rate")
            weeks = (
                (int(rng.integers(28, 34)) if rng.random() < 0.26 else int(rng.integers(34, 37)))
                if preterm
                else int(rng.choice([37, 38, 39, 40, 41], p=[0.07, 0.17, 0.3, 0.3, 0.16]))
            )
            delivery = c + timedelta(days=weeks * 7 + int(rng.integers(0, 7)))
            cur = delivery + timedelta(days=60)
            if c > ctx.end:
                break
            if delivery < ctx.start:
                continue
            age_c = person.age(c)
            if _birth_rate(ctx, person, c) <= 0 or age_c > 49:
                break
            self._plan(ctx, person, c, delivery, weeks)

    def _plan(self, ctx: SimContext, person: Person, c: date, delivery: date, weeks: int) -> None:
        rng = person.rng
        first = rng.random() < 0.40
        gdm = rng.random() < ctx.cal.get("preg.gdm_rate") and not person.has("dm")
        htn = rng.random() < ctx.cal.get("preg.htn_rate")
        severe = htn and rng.random() < 0.4
        cesarean = rng.random() < ctx.cal.get("preg.cesarean_rate")
        info = {
            "c": c,
            "first": first,
            "gdm": gdm,
            "htn": htn,
            "severe": severe,
            "cesarean": cesarean,
            "delivery": delivery,
            "weeks": weeks,
            "gdm_insulin": bool(rng.random() < 0.25),
        }
        for w in (8, 12, 16, 20, 24, 28, 30, 32, 34, 36, 37, 38, 39, 40):
            d = c + timedelta(days=w * 7)
            if (
                d < delivery - timedelta(days=3)
                and ctx.start <= d <= ctx.end
                and rng.random() < min(1.0, ctx.cal.get("preg.prenatal_visits") / 13.0)
            ):
                ctx.schedule(person, d, self.name, "prenatal", w=w, info=info)
        if ctx.start <= delivery <= ctx.end:
            ctx.schedule(person, delivery, self.name, "delivery", info=info)
        pp = delivery + timedelta(days=42)
        if pp <= ctx.end and rng.random() < 0.8:
            ctx.schedule(person, pp, self.name, "postpartum", info=info)

    def handle(
        self, ctx: SimContext, person: Person, day: date, kind: str, payload: dict[str, Any]
    ) -> None:
        getattr(self, f"on_{kind}")(ctx, person, day, **payload)

    def _codes(
        self, ctx: SimContext, person: Person, week: int, info: dict[str, Any], day: date
    ) -> list[str]:
        tri = 1 if week < 14 else 2 if week < 28 else 3
        age = person.age(day)
        supervision = f"Z34.0{tri}" if info["first"] else f"Z34.8{tri}"
        out = []
        if info["htn"] and week >= 30:
            out.append("O14.03" if info["severe"] else "O13.3")
        if info["gdm"] and week >= 26:
            out.append("O24.410" if not info["gdm_insulin"] else "O24.419")
        if age >= ctx.cal.get("preg.high_risk_age"):
            out.append(f"O09.52{tri}" if not info["first"] else "O09.90")
        out.append(supervision)
        out.append("Z3A.01" if week < 8 else f"Z3A.{min(week, 42):02d}")
        return out

    def on_prenatal(
        self, ctx: SimContext, person: Person, day: date, w: int, info: dict[str, Any]
    ) -> None:
        rng = person.rng
        codes = self._codes(ctx, person, w, info, day)
        spec = "obgyn" if rng.random() < 0.85 else pcp_specialty(ctx, person)
        labs: tuple[str, ...] = ()
        if w == 8 or (w == 12 and not person.last_done.get("LAB_PRENATAL_PANEL")):
            labs = ("LAB_PRENATAL_PANEL",)
            person.last_done["LAB_PRENATAL_PANEL"] = day
        if w in (24, 28) and not person.last_done.get("LAB_GLUCOSE_TOLERANCE"):
            labs = labs + ("LAB_GLUCOSE_TOLERANCE",)
            person.last_done["LAB_GLUCOSE_TOLERANCE"] = day
        imaging = ("IMG_OB_ULTRASOUND",) if w in (12, 20) else ()
        visit(
            ctx,
            person,
            day,
            self.name,
            f"prenatal visit, {w} weeks",
            codes,
            specialty=spec,
            level=3,
            labs=labs,
            imaging=imaging,
            capture=0.0,
            telehealth_ok=False,
        )
        if w == 8 or (
            w == 12 and not any(t.drug_key == "prenatal_vitamin" for t in person.therapies)
        ):
            ctx.prescribe(
                person,
                day,
                "prenatal_vitamin",
                "pregnancy",
                codes[-2],
                spec,
                days_supply=30,
                refills=8,
                stop=info["delivery"],
            )
            if rng.random() < 0.12:
                ctx.prescribe(
                    person,
                    day,
                    "ondansetron",
                    "nausea_pregnancy",
                    codes[-2],
                    spec,
                    days_supply=10,
                    refills=0,
                    acute=True,
                    quantity=20,
                )
        if info["htn"] and w >= 30 and rng.random() < 0.5:
            ctx.prescribe(
                person,
                day,
                "labetalol",
                "pregnancy_htn",
                codes[0],
                spec,
                days_supply=30,
                refills=3,
                stop=info["delivery"],
            )
        if info["gdm"] and info["gdm_insulin"] and w >= 28:
            ctx.prescribe(
                person,
                day,
                "insulin_glargine",
                "gdm",
                codes[0],
                spec,
                days_supply=30,
                refills=3,
                stop=info["delivery"],
            )

    def on_delivery(self, ctx: SimContext, person: Person, day: date, info: dict[str, Any]) -> None:
        rng = person.rng
        weeks = info["weeks"]
        principal = "O82" if info["cesarean"] else "O80"
        secondary = ["Z37.0", f"Z3A.{min(weeks, 42):02d}"]
        if info["htn"]:
            secondary.insert(0, "O14.03" if info["severe"] else "O13.3")
        if info["gdm"]:
            secondary.insert(0, "O24.419" if info["gdm_insulin"] else "O24.410")
        if person.age(day) >= ctx.cal.get("preg.high_risk_age"):
            secondary.insert(0, "O09.90")
        if rng.random() < 0.04:
            secondary.insert(0, "O72.1")
        stay = admit_stay(
            ctx,
            person,
            day,
            self.name,
            "delivery",
            principal=[principal, *secondary],  # outcome and weeks of gestation are always coded
            secondary=[],
            pcs=("10D00Z1",) if info["cesarean"] else ("10E0XZZ",),
            surgery_key="DELIVERY_CESAREAN" if info["cesarean"] else "DELIVERY_VAGINAL",
            surgeon="obgyn",
            allow_readmit=False,
        )
        if stay is None:
            return
        person.flags["last_delivery"] = day
        if ctx.spawn_newborn is not None:
            baby = ctx.spawn_newborn(person, day, "cesarean" if info["cesarean"] else "vaginal")
            if baby is not None:
                preterm = [] if weeks >= 37 else [_PRETERM_CODE[weeks]]
                admit_stay(
                    ctx,
                    baby,
                    day,
                    self.name,
                    "newborn",
                    principal=["Z38.01" if info["cesarean"] else "Z38.00", *preterm],
                    secondary=[],
                    los=None
                    if preterm
                    else max(1, (stay.discharge - stay.admit).days)
                    if stay.discharge and stay.admit
                    else 2,
                    newborn=True,
                    allow_readmit=False,
                )

    def on_postpartum(
        self, ctx: SimContext, person: Person, day: date, info: dict[str, Any]
    ) -> None:
        visit(
            ctx,
            person,
            day,
            self.name,
            "postpartum visit",
            ["Z39.2"],
            specialty="obgyn",
            level=3,
            capture=0.2,
            telehealth_ok=False,
        )


# ---------------------------------------------------------------------------------------------
SITE_TABLE = {
    "breast": ("cancer.breast_f_incidence", "F"),
    "prostate": ("cancer.prostate_incidence", "M"),
    "colon": ("cancer.colorectal_incidence", None),
    "lung": ("cancer.lung_incidence", None),
}
DEATH_HAZARD = {"breast": 0.02, "prostate": 0.012, "colon": 0.08, "lung": 0.27}
HISTORY = {"breast": "Z85.3", "prostate": "Z85.46", "colon": "Z85.038", "lung": "Z85.118"}


class CancerModule:
    name = "cancer"

    def start(self, ctx: SimContext, person: Person) -> None:
        m = person.member
        age = m.age(ctx.start)
        years = (ctx.end - ctx.start).days / 365.25
        for site, (rate_key, sex) in SITE_TABLE.items():
            if sex and sex != m.sex:
                continue
            table = ctx.cal.get(rate_key)
            rate = table.get(band(age), 0.0) / 100_000.0
            if site == "lung" and person.has("copd"):
                rate *= 3.0
            if rate <= 0:
                continue
            if person.rng.random() < rate * 0.9:
                # diagnosed in the 330 days before the window opens: still in treatment or follow-up
                ctx.schedule(
                    person,
                    ctx.start,
                    self.name,
                    "begin",
                    site=site,
                    prior=int(person.rng.integers(20, 330)),
                )
                return
            t = person.rng.exponential(1.0 / rate)
            if t < years:
                ctx.schedule(
                    person,
                    ctx.start + timedelta(days=int(t * 365.25) + 1),
                    self.name,
                    "begin",
                    site=site,
                    prior=0,
                )
                return  # one primary cancer in the window

    def handle(
        self, ctx: SimContext, person: Person, day: date, kind: str, payload: dict[str, Any]
    ) -> None:
        getattr(self, f"on_{kind}")(ctx, person, day, **payload)

    # ---- pathway ---------------------------------------------------------------------------------
    def on_begin(
        self, ctx: SimContext, person: Person, day: date, site: str, prior: int = 0
    ) -> None:
        if person.age(day) < 18:
            return
        day = day - timedelta(
            days=prior
        )  # the pathway runs from the diagnosis; dates before the window are not emitted
        rng = person.rng
        cal = ctx.cal
        left = rng.random() < 0.5
        dx = {
            "breast": "C50.912" if left else "C50.911",
            "prostate": "C61",
            "colon": "C18.9",
            "lung": "C34.90",
        }[site]
        pcp = pcp_specialty(ctx, person)
        probs = {
            "surgery": cal.get("cancer.surgery_share")[site],
            "chemo": cal.get("cancer.chemo_share")[site],
        }
        d = day
        # diagnostic work-up
        if site == "breast":
            visit(
                ctx,
                person,
                d,
                self.name,
                "abnormal imaging follow-up",
                ["R92.8"],
                specialty="obgyn" if rng.random() < 0.5 else pcp,
                capture=0.0,
            )
            simple_service(
                ctx,
                person,
                d + timedelta(days=14),
                self.name,
                "breast biopsy",
                ["R92.8"],
                "general_surgery",
                ("BIOPSY_BREAST",),
                setting="outpatient_hospital",
            )
        elif site == "prostate":
            visit(
                ctx,
                person,
                d,
                self.name,
                "elevated PSA",
                ["R97.20"],
                specialty=pcp,
                labs=("LAB_CBC",),
                capture=0.3,
            )
            simple_service(
                ctx,
                person,
                d + timedelta(days=20),
                self.name,
                "prostate biopsy",
                ["R97.20"],
                "urology",
                ("BIOPSY_PROSTATE",),
            )
        elif site == "colon":
            simple_service(
                ctx,
                person,
                d,
                self.name,
                "screening colonoscopy with finding",
                ["Z12.11", "C18.9"],
                "general_surgery",
                ("COLON_SCREEN",),
                setting="asc",
            )
        else:
            visit(
                ctx,
                person,
                d,
                self.name,
                "persistent cough, imaging",
                ["R06.02", "R05"],
                specialty=pcp,
                imaging=("IMG_CT_CHEST",),
                capture=0.2,
            )
            simple_service(
                ctx,
                person,
                d + timedelta(days=10),
                self.name,
                "lung biopsy",
                ["R06.02"],
                "pulmonology",
                ("BIOPSY_LUNG",),
                setting="outpatient_hospital",
            )
        cancer_cond = Cond(f"cancer_{site}", d + timedelta(days=21), dx, data={"site": site})
        person.conds[f"cancer_{site}"] = cancer_cond
        person.flags["cancer"] = [dx]
        oncology_day = d + timedelta(days=21)
        visit(
            ctx,
            person,
            oncology_day,
            self.name,
            "cancer diagnosis and treatment planning",
            [dx],
            specialty="oncology" if site != "prostate" else "urology",
            new=True,
            level=4,
            labs=("LAB_CBC", "LAB_CMP"),
            extra=problem_codes(person, oncology_day),
            telehealth_ok=False,
        )
        staging = {
            "breast": ("IMG_CT_CHEST",),
            "prostate": (),
            "colon": ("IMG_CT_ABD",),
            "lung": ("IMG_PET_CT",),
        }[site]
        if staging:
            simple_service(
                ctx,
                person,
                d + timedelta(days=28),
                self.name,
                "staging imaging",
                [dx],
                "radiology",
                staging,
                setting="imaging",
            )
        # surgery
        surgery_day = d + timedelta(days=int(rng.integers(40, 75)))
        did_surgery = rng.random() < probs["surgery"]
        if did_surgery and surgery_day <= ctx.end:
            pcs, key, spec = {
                "breast": (
                    ("0HTV0ZZ",) if left else ("0HTU0ZZ",),
                    "SURG_MASTECTOMY",
                    "general_surgery",
                ),
                "prostate": (("0VT00ZZ",), "SURG_PROSTATECTOMY", "urology"),
                "colon": (("0DTK0ZZ",), "SURG_COLECTOMY", "general_surgery"),
                "lung": (("0BTC0ZZ",), "SURG_LOBECTOMY", "general_surgery"),
            }[site]
            admit_stay(
                ctx,
                person,
                surgery_day,
                self.name,
                f"{site} cancer surgery",
                principal=[dx],
                secondary=problem_codes(person, surgery_day),
                pcs=pcs,
                surgery_key=key,
                surgeon=spec,
                pa=True,
                complication_p=0.08,
                allow_readmit=False,
            )
        # systemic therapy
        if rng.random() < probs["chemo"]:
            start = (
                (surgery_day + timedelta(days=int(rng.integers(30, 45))))
                if did_surgery
                else d + timedelta(days=45)
            )
            self._chemo(ctx, person, start, site, dx)
        if site in ("breast", "prostate") and rng.random() < (0.55 if site == "breast" else 0.35):
            self._radiation(ctx, person, surgery_day + timedelta(days=40), site, dx)
        # long-term therapy
        if site == "breast" and rng.random() < 0.65:
            drug = (
                "anastrozole"
                if rng.random() < 0.5
                else "letrozole"
                if rng.random() < 0.7
                else "tamoxifen"
            )
            ctx.prescribe(
                person,
                d + timedelta(days=200),
                drug,
                "cancer_breast",
                dx,
                "oncology",
                days_supply=30,
                refills=11,
            )
        if site == "prostate":
            if rng.random() < 0.3:
                ctx.prescribe(
                    person,
                    d + timedelta(days=60),
                    "bicalutamide",
                    "cancer_prostate",
                    dx,
                    "urology",
                    days_supply=30,
                    refills=11,
                )
            if rng.random() < 0.25:
                self._adt(ctx, person, d + timedelta(days=60), dx)
        # follow-up and survivorship
        cur = oncology_day + timedelta(days=120)
        while cur <= ctx.end:
            hist = HISTORY[site]
            active = (cur - day).days < 365
            code = dx if active else hist
            visit(
                ctx,
                person,
                cur,
                self.name,
                "oncology follow-up",
                [code],
                specialty="oncology" if site != "prostate" else "urology",
                level=4,
                labs=("LAB_CBC",) if active else (),
                extra=problem_codes(person, cur),
                telehealth_ok=True,
            )
            cur += timedelta(days=int(rng.integers(90, 180)))
        ctx.schedule(person, day + timedelta(days=365), self.name, "survivor", site=site)
        hazard = DEATH_HAZARD[site]
        t = rng.exponential(1.0 / hazard)
        death = day + timedelta(days=int(t * 365.25) + 60)
        if death <= ctx.end:
            ctx.kill(person, death)
            ctx.schedule(
                person, death - timedelta(days=int(rng.integers(2, 12))), "events", "terminal"
            )
        if site in ("lung", "breast") and rng.random() < (0.35 if site == "lung" else 0.08):
            ctx.schedule(
                person,
                day + timedelta(days=int(rng.integers(150, 500))),
                self.name,
                "mets",
                site=site,
            )

    def on_survivor(self, ctx: SimContext, person: Person, day: date, site: str) -> None:
        if site != "lung" or not person.flags.get("mets"):
            person.flags["cancer"] = [HISTORY[site]]

    def on_mets(self, ctx: SimContext, person: Person, day: date, site: str) -> None:
        code = "C78.00" if site == "lung" else "C79.51"
        person.flags["cancer"] = [code] + person.flags.get("cancer", [])[:1]
        person.flags["mets"] = True
        ed_visit(
            ctx,
            person,
            day,
            self.name,
            "progression with metastases",
            [code],
            level=4,
            extra=problem_codes(person, day),
            imaging=("IMG_CT_CHEST",),
            labs=("LAB_CBC", "LAB_CMP"),
        )

    def _chemo(self, ctx: SimContext, person: Person, start: date, site: str, dx: str) -> None:
        rng = person.rng
        n = int(rng.integers(4, 7)) if site != "lung" else int(rng.integers(4, 9))
        gap = {"breast": 21, "colon": 14, "lung": 21, "prostate": 21}[site]
        regimen = {
            "breast": ["J_PACLITAXEL", "J_CARBOPLATIN"]
            + (["J_TRASTUZUMAB"] if rng.random() < 0.25 else []),
            "colon": ["J_OXALIPLATIN", "J_FLUOROURACIL"],
            "lung": ["J_PEMBROLIZUMAB", "J_CARBOPLATIN", "J_PEMETREXED"],
            "prostate": ["J_CARBOPLATIN"],
        }[site]
        for i in range(n):
            d = start + timedelta(days=gap * i)
            if d > ctx.end:
                break
            keys = (
                ["CHEMO_ADMIN"]
                + regimen
                + (["J_PEGFILGRASTIM"] if rng.random() < 0.35 else [])
                + ["LAB_CBC"]
            )
            dxl = ctx.dx_list(person, d, ["Z51.11", dx], problem_codes(person, d), 0.4)
            enc = ctx.encounter(
                person,
                d,
                "outpatient_hospital",
                "oncology",
                dxl,
                [Svc(k) for k in keys],
                self.name,
                f"chemotherapy cycle {i + 1}",
                prior_auth=(i == 0),
            )
            if enc is not None:
                enc.facility = ctx.directory.pick(person, "hospital", d)
            if i == 0 or rng.random() < 0.5:
                ctx.prescribe(
                    person,
                    d,
                    "ondansetron",
                    "nausea_chemo",
                    dx,
                    "oncology",
                    days_supply=10,
                    refills=0,
                    acute=True,
                    quantity=20,
                )
            if rng.random() < 0.08:
                ctx.encounter(
                    person,
                    d + timedelta(days=int(rng.integers(5, 14))),
                    "lab",
                    "laboratory",
                    ctx.dx_list(person, d, ["D64.81", dx], [], 0),
                    [Svc("LAB_CBC")],
                    self.name,
                    "chemotherapy anemia (labs)",
                )
        if site == "colon" and rng.random() < 0.3:
            ctx.prescribe(
                person,
                start,
                "capecitabine",
                "cancer_colon",
                dx,
                "oncology",
                days_supply=28,
                refills=5,
            )

    def _radiation(self, ctx: SimContext, person: Person, start: date, site: str, dx: str) -> None:
        for week in range(5):
            d = start + timedelta(days=7 * week)
            if d > ctx.end:
                break
            dxl = ctx.dx_list(person, d, ["Z51.0", dx], [], 0)
            enc = ctx.encounter(
                person,
                d,
                "outpatient_hospital",
                "radiation_oncology",
                dxl,
                [Svc("RADIATION_FRACTION", units=5)],
                self.name,
                f"radiation week {week + 1}",
                prior_auth=(week == 0),
            )
            if enc is not None:
                enc.facility = ctx.directory.pick(person, "hospital", d)

    def _adt(self, ctx: SimContext, person: Person, start: date, dx: str) -> None:
        d = start
        while d <= ctx.end:
            simple_service(
                ctx,
                person,
                d,
                self.name,
                "androgen deprivation injection",
                [dx],
                "urology",
                ("J_LEUPROLIDE",),
            )
            d += timedelta(days=90)


# ---------------------------------------------------------------------------------------------
class AcuteModule:
    """Respiratory seasonality, COVID waves, injuries, background symptoms, prevention."""

    name = "acute"

    def start(self, ctx: SimContext, person: Person) -> None:
        m = person.member
        rng = person.rng
        cal = ctx.cal
        age = m.age(ctx.start)
        b = band(age)
        w = cal.get("resp.season_weight")
        for d in ctx.seasonal_dates(
            person, cal.get("resp.acute_per_year")[b], w, ctx.start, ctx.end
        ):
            ctx.schedule(person, d, self.name, "resp")
        waves = cal.get("resp.covid_wave")
        cur = date(ctx.start.year, ctx.start.month, 1)
        while cur <= ctx.end:
            p = waves.get(f"{cur.year}-{cur.month:02d}", 0.003 if cur >= date(2022, 1, 1) else 0.0)
            if rng.random() < p * (1.0 + 0.1 * (age >= 65)):
                nxt = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
                ctx.schedule(
                    person, ctx.rand_day(person, cur, nxt - timedelta(days=1)), self.name, "covid"
                )
            cur = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
        for d in ctx.poisson_days(
            person, cal.get("util.acute_visit_per_year")[b], ctx.start, ctx.end
        ):
            ctx.schedule(person, d, self.name, "acute_visit")
        for d in ctx.poisson_days(
            person, cal.get("util.ed_per_1000")[m.lob] / 1000.0, ctx.start, ctx.end, morbid=True
        ):
            ctx.schedule(person, d, self.name, "ed")
        if 10 <= age <= 60:
            for d in ctx.poisson_days(person, 0.0010, ctx.start, ctx.end):
                ctx.schedule(person, d, self.name, "appendicitis")
        if age >= 18:
            for d in ctx.poisson_days(
                person, 0.0015 * (1.8 if m.sex == "F" else 1.0), ctx.start, ctx.end
            ):
                ctx.schedule(person, d, self.name, "gallbladder")
        if age >= 55:
            rate = 0.007 * (0.5 if m.lob == "medicaid" else 1.0)
            for d in ctx.poisson_days(person, rate, ctx.start, ctx.end):
                ctx.schedule(person, d, self.name, "joint")
        if age >= 55:
            for d in ctx.poisson_days(
                person, 0.016 * (0.6 if m.lob == "medicaid" else 1.0), ctx.start, ctx.end
            ):
                ctx.schedule(person, d, self.name, "cataract")
        for lo, hi in ctx.year_ends():
            self._prevention(ctx, person, lo, hi)

    def _prevention(self, ctx: SimContext, person: Person, lo: date, hi: date) -> None:
        m = person.member
        rng = person.rng
        cal = ctx.cal
        age = m.age(lo)
        wp = cal.get("prev.wellness_visit")[m.lob]
        if age < 2:
            for k in range(4):
                if rng.random() < 0.85:
                    ctx.schedule(
                        person,
                        ctx.rand_day(
                            person, lo + timedelta(days=k * 90), lo + timedelta(days=k * 90 + 80)
                        ),
                        self.name,
                        "wellness",
                    )
        elif rng.random() < (wp + 0.15 if age < 18 else wp):
            ctx.schedule(person, ctx.rand_day(person, lo, hi), self.name, "wellness")
        if rng.random() < cal.get("flu_vaccine.rate")[band(age)]:
            ctx.schedule(
                person,
                ctx.rand_day(person, max(lo, date(lo.year, 9, 15)), date(lo.year, 11, 30)),
                self.name,
                "flu_shot",
            )
        if (
            m.sex == "F"
            and 50 <= age <= 74
            and rng.random() < 0.49 * cal.get("prev.mammogram_biennial") / 0.74
        ):
            ctx.schedule(person, ctx.rand_day(person, lo, hi), self.name, "mammogram")
        if 45 <= age <= 75 and rng.random() < 0.108 * cal.get("prev.colorectal_10y") / 0.68:
            ctx.schedule(person, ctx.rand_day(person, lo, hi), self.name, "colonoscopy")
        if (
            m.sex == "F"
            and 21 <= age <= 64
            and rng.random() < 0.36 * cal.get("prev.cervical_3y") / 0.74
        ):
            ctx.schedule(person, ctx.rand_day(person, lo, hi), self.name, "cervical")
        if m.sex == "M" and 55 <= age <= 69 and rng.random() < cal.get("prev.psa_screen_annual"):
            ctx.schedule(person, ctx.rand_day(person, lo, hi), self.name, "psa")

    def handle(
        self, ctx: SimContext, person: Person, day: date, kind: str, payload: dict[str, Any]
    ) -> None:
        getattr(self, f"on_{kind}")(ctx, person, day, **payload)

    # ---- prevention ------------------------------------------------------------------------------
    def on_wellness(self, ctx: SimContext, person: Person, day: date) -> None:
        age = person.age(day)
        rng = person.rng
        extras: list[str] = []
        if age < 18:
            codes = ["Z00.129"]
            if age < 1 and (day - person.member.dob).days < 29:
                codes = ["Z00.111"] if (day - person.member.dob).days >= 8 else ["Z00.110"]
            visit(
                ctx,
                person,
                day,
                self.name,
                "well-child visit",
                codes,
                specialty="pediatrics",
                level=3,
                telehealth_ok=False,
                capture=0.2,
            )
            return
        codes = ["Z00.00"]
        labs: tuple[str, ...] = ()
        if age >= 40 and rng.random() < 0.35:
            labs = ("LAB_LIPID_PANEL",)
            extras.append("Z13.220")
        if age >= 18 and rng.random() < 0.3:
            extras.append("Z13.31")
        svcs: list[str] = []
        key = None
        if person.member.lob == "ma" or age >= 65:
            key = "AWV_SUBSEQ" if person.last_done.get("AWV") else "AWV_INITIAL"
            person.last_done["AWV"] = day
        if rng.random() < 0.4 and age >= 18:
            svcs.append("DEPRESSION_SCREEN")
        if (
            person.has("obesity")
            and person.conds["obesity"].data.get("coded")
            and rng.random() < 0.1
        ):
            svcs.append("OBESITY_COUNSEL")
        enc = visit(
            ctx,
            person,
            day,
            self.name,
            "annual preventive visit",
            codes + extras,
            level=3,
            labs=labs,
            extra=problem_codes(person, day),
            capture=0.5,
            telehealth_ok=False,
            extra_services=tuple(svcs),
        )
        if enc is not None and key:
            enc.services = [Svc(key)] + [s for s in enc.services if not s.key.startswith("EM_PREV")]

    def on_flu_shot(self, ctx: SimContext, person: Person, day: date) -> None:
        if person.age(day) < 1:
            return
        simple_service(
            ctx,
            person,
            day,
            self.name,
            "influenza vaccination",
            ["Z23"],
            pcp_specialty(ctx, person),
            ("FLU_VACCINE", "FLU_VACCINE_ADMIN"),
            setting="office",
        )

    def on_mammogram(self, ctx: SimContext, person: Person, day: date) -> None:
        simple_service(
            ctx,
            person,
            day,
            self.name,
            "screening mammogram",
            ["Z12.31"],
            "radiology",
            ("MAMMO_SCREEN",),
            setting="imaging",
        )

    def on_colonoscopy(self, ctx: SimContext, person: Person, day: date) -> None:
        enc = simple_service(
            ctx,
            person,
            day,
            self.name,
            "screening colonoscopy",
            ["Z12.11"] + (["D12.6"] if person.rng.random() < 0.25 else []),
            "general_surgery",
            ("COLON_SCREEN",),
            setting="asc",
        )
        if enc is not None:
            enc.facility = ctx.directory.pick(person, "hospital", day)

    def on_cervical(self, ctx: SimContext, person: Person, day: date) -> None:
        simple_service(
            ctx,
            person,
            day,
            self.name,
            "cervical cancer screening",
            ["Z12.4"],
            "obgyn",
            ("PAP_COLLECT",),
        )

    def on_psa(self, ctx: SimContext, person: Person, day: date) -> None:
        simple_service(
            ctx,
            person,
            day,
            self.name,
            "PSA screening",
            ["Z12.5"],
            "laboratory",
            ("PSA_SCREEN",),
            setting="lab",
        )

    # ---- respiratory -----------------------------------------------------------------------------
    def on_resp(self, ctx: SimContext, person: Person, day: date) -> None:
        rng = person.rng
        age = person.age(day)
        month_w = ctx.cal.get("resp.season_weight")[day.month]
        if age < 2:
            table = [
                ("J21.9", 0.15),
                ("J06.9", 0.45),
                ("H66.90", 0.15),
                ("R50.9", 0.1),
                ("J11.1", 0.08),
                ("B34.9", 0.07),
            ]
        elif age < 18:
            table = [
                ("J06.9", 0.38),
                ("J02.9", 0.12),
                ("J02.0", 0.10),
                ("H66.90", 0.10),
                ("J01.90", 0.06),
                ("J20.9", 0.06),
                ("cough", 0.06),
                ("J11.1", 0.12),
            ]
        else:
            flu = ctx.cal.get("resp.flu_share") * (month_w / 1.2)
            table = [
                ("J06.9", 0.35),
                ("J20.9", 0.15),
                ("J01.90", 0.10),
                ("J02.9", 0.07),
                ("J02.0", 0.03),
                ("cough", 0.06),
                ("J11.1", flu),
                ("J18.9", 0.02 + 0.04 * (age >= 65)),
            ]
        total = sum(p for _, p in table)
        r = rng.random() * total
        acc = 0.0
        dx = table[-1][0]
        for code, p in table:
            acc += p
            if r < acc:
                dx = code
                break
        if dx == "J11.1" and rng.random() < 0.4:
            dx = "J10.1"
        problems = problem_codes(person, day)
        spec = pcp_specialty(ctx, person)
        labs: tuple[str, ...] = ()
        if dx in ("J10.1", "J11.1") and rng.random() < 0.6:
            labs = ("LAB_FLU_COVID_PCR",)
        if dx == "J02.0":
            labs = ("LAB_STREP_RAPID",)
        if dx == "J18.9":
            admit_p = 0.55 if age >= 65 else 0.18
            if rng.random() < admit_p:
                ed_visit(
                    ctx,
                    person,
                    day,
                    self.name,
                    "pneumonia",
                    [dx],
                    level=5,
                    extra=problems,
                    imaging=("IMG_CHEST_XRAY",),
                    labs=("LAB_CBC",),
                    admit={"principal": [dx], "secondary": problems, "complication_p": 0.14},
                    ambulance=0.25,
                )
                return
        r2 = rng.random()
        ed_p = 0.10 if age >= 75 else 0.08 if age < 5 else 0.04
        if r2 < ed_p:
            ed_visit(
                ctx,
                person,
                day,
                self.name,
                "respiratory illness",
                [dx],
                extra=problems,
                labs=labs,
                imaging=("IMG_CHEST_XRAY",) if dx == "J18.9" else (),
                ambulance=0.05,
            )
            enc_dx = dx
        else:
            setting = "urgent" if r2 < ed_p + 0.22 else None
            visit(
                ctx,
                person,
                day,
                self.name,
                "respiratory illness",
                [dx],
                specialty=spec,
                setting=setting,
                extra=problems,
                labs=labs,
                level=3,
                imaging=("IMG_CHEST_XRAY",) if dx == "J18.9" else (),
            )
            enc_dx = dx
        self._respiratory_rx(ctx, person, day, enc_dx, spec)

    def _respiratory_rx(
        self, ctx: SimContext, person: Person, day: date, dx: str, spec: str
    ) -> None:
        rng = person.rng

        def abx(drug: str, indication: str, days: int, qty: float) -> None:
            ctx.prescribe(
                person,
                day,
                drug,
                indication,
                dx if dx != "cough" else "R05.9",
                spec,
                days_supply=days,
                refills=0,
                acute=True,
                quantity=qty,
            )

        if dx == "J06.9" and rng.random() < 0.15:
            abx("amoxicillin", "uri_bacterial", 7, 21)
        elif dx == "J20.9" and rng.random() < 0.5:
            abx("azithromycin" if rng.random() < 0.6 else "doxycycline", "uri_bacterial", 5, 6)
        elif dx == "J01.90" and rng.random() < 0.8:
            abx("amox_clav" if rng.random() < 0.6 else "doxycycline", "uri_bacterial", 10, 20)
        elif dx == "J02.0" and rng.random() < 0.95:
            abx("amoxicillin", "strep", 10, 20)
        elif dx == "H66.90" and rng.random() < 0.9:
            abx("amoxicillin", "uri_bacterial", 10, 20)
        elif dx in ("J10.1", "J11.1") and rng.random() < 0.45:
            ctx.prescribe(
                person,
                day,
                "oseltamivir",
                "flu",
                dx,
                spec,
                days_supply=5,
                refills=0,
                acute=True,
                quantity=10,
            )
        elif dx == "J18.9":
            abx("azithromycin", "pneumonia", 5, 6)
        if dx in ("cough", "J06.9", "J20.9") and rng.random() < 0.12:
            ctx.prescribe(
                person,
                day,
                "benzonatate",
                "cough" if dx == "cough" else "uri_viral",
                dx if dx != "cough" else "R05.9",
                spec,
                days_supply=7,
                refills=0,
                acute=True,
                quantity=21,
            )

    def on_covid(self, ctx: SimContext, person: Person, day: date) -> None:
        rng = person.rng
        age = person.age(day)
        problems = problem_codes(person, day)
        if age >= 65 and rng.random() < 0.06 or (age >= 45 and rng.random() < 0.015):
            ed_visit(
                ctx,
                person,
                day,
                self.name,
                "COVID-19 pneumonia",
                ["J12.82", "U07.1"],
                level=5,
                extra=problems,
                imaging=("IMG_CHEST_XRAY",),
                labs=("LAB_FLU_COVID_PCR",),
                admit={
                    "principal": ["J12.82"],
                    "secondary": ["U07.1"] + problems,
                    "complication_p": 0.2,
                },
                ambulance=0.3,
            )
        else:
            visit(
                ctx,
                person,
                day,
                self.name,
                "COVID-19",
                ["U07.1"],
                specialty=pcp_specialty(ctx, person),
                extra=problems,
                labs=("LAB_FLU_COVID_PCR",),
                level=3,
            )

    # ---- acute non-respiratory and ED ----------------------------------------------------------
    def on_acute_visit(self, ctx: SimContext, person: Person, day: date) -> None:
        rng = person.rng
        age = person.age(day)
        sex = person.member.sex
        spec = pcp_specialty(ctx, person)
        problems = problem_codes(person, day)
        if age < 18:
            table = [
                ("L30.9", 0.30),
                ("A08.4", 0.20),
                ("R50.9", 0.20),
                ("S93.401A", 0.10),
                ("S61.411A", 0.08),
                ("R42", 0.02),
            ]
        else:
            table = [
                ("low_back_pain", 0.22),
                ("M25.561", 0.06),
                ("M25.562", 0.06),
                ("headache", 0.08),
                ("G43.909", 0.04),
                ("R53.83", 0.06),
                ("R42", 0.04),
                ("L30.9", 0.05),
                ("N39.0", 0.20 if sex == "F" else 0.02),
                ("L03.90", 0.03),
                ("A08.4", 0.04),
                ("G47.00", 0.03),
                ("J30.9", 0.04),
                ("S93.402A", 0.05),
                ("D50.9", 0.02),
            ]
            if age >= 45:
                table.append(("M17.11" if rng.random() < 0.5 else "M17.12", 0.06))
        total = sum(p for _, p in table)
        r = rng.random() * total
        acc = 0.0
        dx = table[-1][0]
        for code, p in table:
            acc += p
            if r < acc:
                dx = code
                break
        labs: tuple[str, ...] = ()
        imaging: tuple[str, ...] = ()
        if dx == "N39.0":
            labs = ("LAB_URINALYSIS",)
        elif dx == "R53.83":
            labs = ("LAB_CBC", "LAB_TSH")
        elif dx == "D50.9":
            labs = ("LAB_CBC",)
        elif dx.startswith(("M25", "M17", "S93")):
            imaging = ("IMG_XRAY_EXTREMITY",) if rng.random() < 0.3 else ()
        elif dx == "low_back_pain" and rng.random() < 0.05:
            imaging = ("IMG_MRI_SPINE",)
        setting = "urgent" if dx.startswith(("S93", "S61", "A08")) and rng.random() < 0.5 else None
        specialty = "orthopedics" if dx in ("low_back_pain",) and rng.random() < 0.1 else spec
        if dx == "L03.90" and rng.random() < 0.03:
            ed_visit(
                ctx,
                person,
                day,
                self.name,
                "cellulitis",
                [dx],
                extra=problems,
                level=4,
                admit={"principal": [dx], "secondary": problems},
            )
            return
        enc = visit(
            ctx,
            person,
            day,
            self.name,
            "acute visit",
            [dx],
            specialty=specialty,
            setting=setting,
            extra=problems,
            labs=labs,
            imaging=imaging,
            level=3,
            capture=0.5,
        )
        if enc is None:
            return
        code = enc.dx[0][0]
        if dx in ("low_back_pain",) or dx.startswith("M"):
            if rng.random() < 0.40:
                ctx.prescribe(
                    person,
                    day,
                    "naproxen",
                    "pain_acute",
                    code,
                    spec,
                    days_supply=10,
                    refills=0,
                    acute=True,
                    quantity=40,
                )
            if rng.random() < 0.25 and age >= 18:
                ctx.prescribe(
                    person,
                    day,
                    "cyclobenzaprine",
                    "pain_acute",
                    code,
                    spec,
                    days_supply=10,
                    refills=0,
                    acute=True,
                    quantity=30,
                )
            if rng.random() < 0.05 and age >= 18:
                ctx.prescribe(
                    person,
                    day,
                    "tramadol",
                    "pain_acute",
                    code,
                    spec,
                    days_supply=5,
                    refills=0,
                    acute=True,
                    quantity=20,
                )
        elif dx == "N39.0" and rng.random() < 0.8 and sex == "F":
            ctx.prescribe(
                person,
                day,
                "nitrofurantoin",
                "uti",
                code,
                spec,
                days_supply=5,
                refills=0,
                acute=True,
                quantity=10,
            )
        elif dx == "L03.90" and rng.random() < 0.85:
            ctx.prescribe(
                person,
                day,
                "cephalexin",
                "skin_infection",
                code,
                spec,
                days_supply=7,
                refills=0,
                acute=True,
                quantity=28,
            )
        elif dx == "A08.4" and rng.random() < 0.3:
            ctx.prescribe(
                person,
                day,
                "ondansetron",
                "gastroenteritis",
                code,
                spec,
                days_supply=3,
                refills=0,
                acute=True,
                quantity=10,
            )
        elif dx == "G47.00" and rng.random() < 0.2 and age >= 18:
            ctx.prescribe(
                person,
                day,
                "trazodone",
                "insomnia",
                code,
                spec,
                days_supply=30,
                refills=1,
                acute=True,
                quantity=30,
            )

    def on_ed(self, ctx: SimContext, person: Person, day: date) -> None:
        rng = person.rng
        age = person.age(day)
        problems = problem_codes(person, day)
        table = [
            ("injury", 0.30),
            ("R07.9", 0.12),
            ("R10.9", 0.12),
            ("headache", 0.06),
            ("N39.0", 0.06 if person.member.sex == "F" else 0.01),
            ("low_back_pain", 0.06),
            ("R55", 0.04),
            ("A08.4", 0.06),
            ("L03.90", 0.04),
            ("R50.9", 0.10 if age < 12 else 0.02),
        ]
        total = sum(p for _, p in table)
        r = rng.random() * total
        acc = 0.0
        dx = table[0][0]
        for code, p in table:
            acc += p
            if r < acc:
                dx = code
                break
        if dx == "injury":
            dx = _pick_one(person, ["S93.401A", "S93.402A", "S61.411A", "S06.0X0A", "S82.201A"])
        labs: tuple[str, ...] = ()
        imaging: tuple[str, ...] = ()
        admit = None
        if dx == "R07.9":
            labs, imaging = ("LAB_BMP", "LAB_CBC"), ("IMG_CHEST_XRAY",)
        elif dx == "R10.9":
            labs, imaging = (
                ("LAB_CMP", "LAB_CBC", "LAB_URINALYSIS"),
                ("IMG_CT_ABD",) if rng.random() < 0.4 else (),
            )
        elif dx.startswith(("S93", "S82")):
            imaging = ("IMG_XRAY_EXTREMITY",)
        elif dx == "N39.0":
            labs = ("LAB_URINALYSIS",)
            if rng.random() < 0.06:
                admit = {"principal": [dx], "secondary": problems}
        elif dx == "L03.90" and rng.random() < 0.08:
            admit = {"principal": [dx], "secondary": problems}
        elif dx == "A08.4":
            labs = ("LAB_BMP",)
        ed_visit(
            ctx,
            person,
            day,
            self.name,
            "emergency visit",
            [dx],
            extra=problems,
            labs=labs,
            imaging=imaging,
            admit=admit,
            capture=0.3,
        )
        if dx.startswith("S82"):
            visit(
                ctx,
                person,
                day + timedelta(days=int(rng.integers(7, 21))),
                self.name,
                "fracture follow-up",
                ["S82.201D"],
                specialty="orthopedics",
                level=3,
                capture=0.0,
            )
        if dx.startswith("S93") and rng.random() < 0.3:
            visit(
                ctx,
                person,
                day + timedelta(days=int(rng.integers(7, 21))),
                self.name,
                "sprain follow-up",
                ["S93.401D"] if dx.endswith("1A") else ["S93.402A"],
                specialty=pcp_specialty(ctx, person),
                level=3,
                capture=0.0,
            )

    def on_joint(self, ctx: SimContext, person: Person, day: date) -> None:
        rng = person.rng
        hip = rng.random() < 0.4
        right = rng.random() < 0.5
        dx = ("M16.11" if right else "M16.12") if hip else ("M17.11" if right else "M17.12")
        pcs = (("0SR90JZ" if right else "0SRB0JZ") if hip else ("0SRC0JZ" if right else "0SRD0JZ"),)
        problems = problem_codes(person, day)
        visit(
            ctx,
            person,
            day - timedelta(days=int(rng.integers(14, 45))),
            self.name,
            "pre-operative evaluation",
            [dx],
            specialty="orthopedics",
            level=4,
            extra=problems,
            imaging=("IMG_XRAY_EXTREMITY",),
            telehealth_ok=False,
        )
        admit_stay(
            ctx,
            person,
            day,
            self.name,
            "elective joint replacement",
            principal=[dx],
            secondary=[c for c in problems if c != dx],
            pcs=pcs,
            surgery_key="SURG_JOINT_REPLACEMENT",
            surgeon="orthopedics",
            pa=True,
            complication_p=0.04,
            allow_readmit=False,
        )

    def on_cataract(self, ctx: SimContext, person: Person, day: date) -> None:
        enc = simple_service(
            ctx,
            person,
            day,
            self.name,
            "cataract surgery",
            ["H25.9"],
            "ophthalmology",
            ("CATARACT_SURGERY",),
            setting="asc",
            extra=problem_codes(person, day),
        )
        if enc is not None:
            enc.facility = ctx.directory.pick(person, "hospital", day)

    def on_appendicitis(self, ctx: SimContext, person: Person, day: date) -> None:
        problems = problem_codes(person, day)
        ed_visit(
            ctx,
            person,
            day,
            self.name,
            "acute appendicitis",
            ["K35.80"],
            level=5,
            extra=problems,
            labs=("LAB_CBC", "LAB_CMP"),
            imaging=("IMG_CT_ABD",),
            admit={
                "principal": ["K35.80"],
                "secondary": problems,
                "pcs": ("0DTJ4ZZ",),
                "surgery_key": "SURG_APPENDECTOMY",
                "surgeon": "general_surgery",
            },
            ambulance=0.15,
        )

    def on_gallbladder(self, ctx: SimContext, person: Person, day: date) -> None:
        problems = problem_codes(person, day)
        acute = person.rng.random() < 0.45
        principal = "K81.0" if acute else "K80.20"
        if acute:
            ed_visit(
                ctx,
                person,
                day,
                self.name,
                "acute cholecystitis",
                [principal],
                level=4,
                extra=problems,
                labs=("LAB_CBC", "LAB_CMP"),
                imaging=("IMG_CT_ABD",),
                admit={
                    "principal": [principal],
                    "secondary": problems,
                    "pcs": ("0FT44ZZ",),
                    "surgery_key": "SURG_CHOLECYSTECTOMY",
                    "surgeon": "general_surgery",
                },
            )
        else:
            admit_stay(
                ctx,
                person,
                day,
                self.name,
                "elective cholecystectomy",
                principal=[principal],
                secondary=problems,
                pcs=("0FT44ZZ",),
                surgery_key="SURG_CHOLECYSTECTOMY",
                surgeon="general_surgery",
                pa=True,
            )


def _pick_one(person: Person, options: list[str]) -> str:
    return options[int(person.rng.integers(0, len(options)))]


# ---------------------------------------------------------------------------------------------
class EventsModule:
    """Readmissions, terminal stays and background medical admissions."""

    name = "events"

    def start(self, ctx: SimContext, person: Person) -> None:
        m = person.member
        rng = person.rng
        cal = ctx.cal
        if (
            m.death is not None
            and ctx.start <= m.death <= ctx.end
            and not person.flags.get("terminal_set")
        ):
            if rng.random() < cal.get("util.death_in_hospital_share"):
                ctx.schedule(
                    person, m.death - timedelta(days=int(rng.integers(1, 9))), self.name, "terminal"
                )
        rate = cal.get("util.admit_background_per_1000")[band(m.age(ctx.start))] / 1000.0
        for d in ctx.poisson_days(person, rate, ctx.start, ctx.end, morbid=True):
            ctx.schedule(person, d, self.name, "background")

    def handle(
        self, ctx: SimContext, person: Person, day: date, kind: str, payload: dict[str, Any]
    ) -> None:
        getattr(self, f"on_{kind}")(ctx, person, day, **payload)

    def on_readmit(
        self,
        ctx: SimContext,
        person: Person,
        day: date,
        index: int,
        principal: list[str],
        secondary: list[str],
    ) -> None:
        ed_visit(
            ctx,
            person,
            day,
            self.name,
            "30-day readmission",
            principal[:1],
            level=4,
            extra=secondary,
            admit={
                "principal": principal,
                "secondary": secondary,
                "readmit_of": index,
                "allow_readmit": False,
            },
            ambulance=0.2,
        )

    def on_terminal(self, ctx: SimContext, person: Person, day: date) -> None:
        m = person.member
        if person.flags.get("terminal_done"):
            return
        person.flags["terminal_done"] = True
        problems = problem_codes(person, day)
        principal = "I50.9" if person.has("hf") else "J44.1" if person.has("copd") else "A41.9"
        if principal == "I50.22":
            principal = "I50.9"
        stay_days = int(person.rng.integers(2, 8))
        end = m.death or day
        start = max(ctx.start, end - timedelta(days=stay_days))
        admit_stay(
            ctx,
            person,
            start,
            self.name,
            "terminal admission",
            principal=[principal],
            secondary=[c for c in problems if c != principal],
            los=max(1, (end - start).days),
            status="20",
            allow_readmit=False,
            via_ed=True,
        )

    def on_background(self, ctx: SimContext, person: Person, day: date) -> None:
        rng = person.rng
        problems = problem_codes(person, day)
        age = person.age(day)
        options: list[tuple[str, float]] = [
            ("N39.0", 0.22),
            ("L03.90", 0.15),
            ("J18.9", 0.20),
            ("A41.9", 0.12),
        ]
        if person.has("afib"):
            options.append(("I48.91", 0.10))
        if person.has("ckd") or person.has("dm"):
            options.append(("N17.9", 0.10))
        if (person.has("htn") or person.has("afib")) and age >= 45:
            options.append(("I63.9", 0.06))
        if person.has("cad") or (person.has("htn") and age >= 45):
            options.append(("I21.4", 0.06))
        if person.has("dm"):
            options.append(
                ("E10.65" if person.conds["dm"].data["type"] == "E10" else "E11.65", 0.07)
            )
        if person.has("hf"):
            options.append(("I50.9", 0.12))
        if person.has("copd"):
            options.append(("J44.1", 0.12))
        total = sum(p for _, p in options)
        r = rng.random() * total
        acc = 0.0
        principal = options[0][0]
        for code, p in options:
            acc += p
            if r < acc:
                principal = code
                break
        secondary = [c for c in problems if c != principal]
        icu = (
            principal == "A41.9" and rng.random() < 0.10
        )  # septic shock needing prolonged ventilation
        pcs = ("5A1955Z",) if icu else ()
        if rng.random() < 0.8:
            ed_visit(
                ctx,
                person,
                day,
                self.name,
                "emergency admission",
                [principal],
                level=5,
                extra=secondary,
                admit={
                    "principal": [principal],
                    "secondary": secondary,
                    "complication_p": 0.10,
                    "pcs": pcs,
                },
                labs=("LAB_CBC", "LAB_CMP"),
                ambulance=0.3,
            )
        else:
            admit_stay(
                ctx,
                person,
                day,
                self.name,
                "direct admission",
                principal=[principal],
                secondary=secondary,
                complication_p=0.08,
                pcs=pcs,
            )


EVENT_MODULES = (PregnancyModule, CancerModule, AcuteModule, EventsModule)
