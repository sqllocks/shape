"""Building blocks the clinical modules share: visits, labs, ED visits and inpatient stays.

A stay's length is drawn around the DRG's geometric mean, and the DRG itself is *derived* from the
principal diagnosis family and the CC/MCC secondary diagnoses on the stay, so length of stay and
DRG agree by construction.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

from .engine import SimContext
from .icd10cm import ICD10CM
from .model import Encounter, Person, Svc
from .reference import CC_CODES, DRG, DRG_FAMILIES, MCC_CODES
from .services import SERVICES

# principal diagnosis (code prefix or code) -> DRG family
PRINCIPAL_FAMILY: tuple[tuple[str, str], ...] = (
    ("J18", "pneumonia"), ("J12", "pneumonia"), ("J44", "copd"), ("J45", "copd"), ("I50", "heart_failure"),
    ("A41", "sepsis"), ("N39", "uti"), ("E11", "diabetes"), ("E10", "diabetes"), ("N17", "renal_failure"),
    ("N18", "renal_failure"), ("I48", "arrhythmia"), ("L03", "cellulitis"), ("F20", "psychosis"),
    ("F31", "psychosis"), ("F33", "psychosis"), ("F32", "psychosis"), ("F10", "substance"),
    ("F11", "substance"), ("I21", "mi"), ("I63", "stroke"), ("K35", "appendectomy"), ("K81", "cholecystectomy"), ("K80", "cholecystectomy"),
    ("O80", "vaginal_delivery"), ("O82", "cesarean"), ("Z38", "newborn"), ("C50", "mastectomy"),
    ("C61", "prostatectomy"), ("C18", "colectomy"), ("C34", "lobectomy"), ("J96", "pneumonia"),
)
POA_EXEMPT = ("Z79", "Z68", "Z37", "Z38", "Z3A", "Z87", "Z85", "Z99", "Z17", "O80", "O82", "Z51")

LOS_SIGMA = 0.5


def family_of(principal: str) -> str:
    for prefix, fam in PRINCIPAL_FAMILY:
        if principal.startswith(prefix):
            return fam
    return "sepsis"


def severity_tier(secondary: list[str]) -> int:
    if any(c in MCC_CODES for c in secondary):
        return 0
    if any(c in CC_CODES for c in secondary):
        return 1
    return 2


def assign_drg(dx: list[tuple[str, str]]) -> str:
    codes = [c for c, _ in dx]
    if codes[0].startswith("Z38"):
        very = any(c in ("P07.31", "P07.32", "P07.33", "P07.34", "P07.35", "P07.36") for c in codes[1:])
        if very:
            return DRG_FAMILIES["very_premature"][0]
        if any(c.startswith("P07") for c in codes[1:]):
            return DRG_FAMILIES["premature"][0]
    fam = family_of(codes[0])
    return DRG_FAMILIES[fam][severity_tier(codes[1:])]


def poa(code: str, hospital_acquired: bool = False) -> str:
    if code.startswith(POA_EXEMPT):
        return ""
    return "N" if hospital_acquired else "Y"


def em_key(level: int, new: bool = False) -> str:
    if new:
        return "EM_OFFICE_NEW_4" if level >= 4 else "EM_OFFICE_NEW_3"
    return f"EM_OFFICE_EST_{max(2, min(4, level))}"


def pcp_specialty(ctx: SimContext, person: Person) -> str:
    pcp = person.member.pcp
    return ctx.directory.info(pcp).specialty if pcp is not None else "family_medicine"


def visit(
    ctx: SimContext, person: Person, day: date, module: str, reason: str, primary: list[str],
    *, specialty: str | None = None, level: int = 3, new: bool = False, extra: list[str] | None = None,
    labs: tuple[str, ...] = (), extra_services: tuple[str, ...] = (), capture: float = 0.65,
    telehealth_ok: bool = True, setting: str | None = None, imaging: tuple[str, ...] = (),
) -> Encounter | None:
    """An office, telehealth or urgent-care visit with optional same-day labs and imaging."""
    m = person.member
    spec = specialty or pcp_specialty(ctx, person)
    if m.age(day) < 16 and spec in ("family_medicine", "internal_medicine"):
        spec = "pediatrics" if specialty is None else spec
    if m.age(day) >= 18 and spec == "pediatrics":
        spec = "family_medicine"
    dx = ctx.dx_list(person, day, primary, extra, capture)
    if not dx:
        return None
    tele = (
        setting is None and telehealth_ok and day >= date(2020, 3, 1)
        and spec not in ("emergency_medicine",) and person.rng.random() < 0.10
    )
    kind = setting or ("telehealth" if tele else "office")
    key = "EM_TELE_EST" if kind == "telehealth" else (
        "EM_URGENT_3" if kind == "urgent" else em_key(level, new)
    )
    if kind == "telehealth":
        extra_services = tuple(k for k in extra_services if {"02", "10"} & set(SERVICES[k].pos))
    services = [Svc(key)] + [Svc(k) for k in extra_services]
    enc = ctx.encounter(person, day, kind, spec, dx, services, module, reason)
    if enc is None:
        return None
    if labs:
        lab_services = [Svc(k) for k in labs if _ok(person, k, day)]
        if lab_services:
            ctx.encounter(person, day + timedelta(days=int(person.rng.integers(0, 3))), "lab",
                          "laboratory", dx[:2], lab_services, module, reason + " (labs)")
            for k in labs:
                person.last_done[k] = day
    if imaging:
        ctx.encounter(person, day + timedelta(days=int(person.rng.integers(0, 6))), "imaging",
                      "radiology", dx[:2], [Svc(k) for k in imaging if _ok(person, k, day)],
                      module, reason + " (imaging)")
    return enc


def _ok(person: Person, key: str, day: date) -> bool:
    s = SERVICES[key]
    m = person.member
    age = m.age(day)
    return not (s.sex and s.sex != m.sex) and s.age_min <= age <= s.age_max


def due(person: Person, key: str, day: date, days: int, p: float = 0.8) -> bool:
    last = person.last_done.get(key)
    return (last is None or (day - last).days >= days) and bool(person.rng.random() < p)


def level_for(person: Person, complexity: float = 0.0) -> int:
    r = person.rng.random() - complexity
    return 4 if r < 0.22 else 3 if r < 0.78 else 2


def ed_visit(
    ctx: SimContext, person: Person, day: date, module: str, reason: str, primary: list[str],
    *, extra: list[str] | None = None, level: int | None = None, labs: tuple[str, ...] = (),
    imaging: tuple[str, ...] = (), admit: dict[str, Any] | None = None, capture: float = 0.5,
    ambulance: float = 0.1,
) -> Encounter | None:
    """An ED visit (facility fee plus physician), possibly arriving by ambulance, possibly admitted."""
    dx = ctx.dx_list(person, day, primary, extra, capture)
    if not dx:
        return None
    lvl = level or (3 if person.rng.random() < 0.40 else 4 if person.rng.random() < 0.7 else 5)
    services = [Svc(f"ED_FACILITY_{lvl}"), Svc(f"EM_ED_{lvl}", specialty="emergency_medicine")]
    services += [Svc(k) for k in labs if _ok(person, k, day)]
    services += [Svc(k) for k in imaging if _ok(person, k, day)]
    if admit is not None:
        # the ED stay is folded into the admission; the ED physician still bills separately
        services = [Svc(f"EM_ED_{lvl}", specialty="emergency_medicine")]
    enc = ctx.encounter(person, day, "ed", "emergency_medicine", dx, services, module, reason,
                        emergency=True)
    if enc is None:
        return None
    if person.rng.random() < ambulance:
        als = person.rng.random() < 0.55
        ctx.encounter(person, day, "transport", "ambulance", dx[:1],
                      [Svc("AMBULANCE_ALS" if als else "AMBULANCE_BLS"), Svc("AMBULANCE_MILEAGE")],
                      module, reason + " (ambulance)", emergency=True)
    if admit is not None:
        stay = admit_stay(ctx, person, day, module, reason, via_ed=True, **admit)
        if stay is not None:
            enc.parent_stay = stay.eid
    return enc


def draw_los(person: Person, gmlos: float, lo: int, hi: int) -> int:
    z = person.rng.normal(math.log(gmlos), LOS_SIGMA)
    return int(max(lo, min(hi, round(math.exp(z)))))


def admit_stay(
    ctx: SimContext, person: Person, day: date, module: str, reason: str, *, principal: list[str],
    secondary: list[str] | None = None, pcs: tuple[str, ...] = (), via_ed: bool = False,
    surgeon: str | None = None, surgery_key: str | None = None, los: int | None = None,
    complication_p: float = 0.0, readmit_of: int | None = None, pa: bool = False,
    status: str = "01", allow_readmit: bool = True, newborn: bool = False, hospital_extra: float = 1.0,
) -> Encounter | None:
    """An inpatient stay: derive DRG and length of stay, add hospitalist or surgeon professional
    services, and maybe a 30-day readmission."""
    primary = ctx.dx_list(person, day, principal, secondary, capture=0.9, limit=9)
    if not primary:
        return None
    dx = [(c, "Y") for c, _ in primary]
    codes = [c for c, _ in dx]
    # hospital-acquired complications arise in the stay (POA N); they change the DRG tier
    if complication_p and person.rng.random() < complication_p:
        for extra in ("J96.01", "N17.9", "A41.9"):
            c = ctx.code(person, extra, day)
            if c and c not in codes and extra not in codes:
                dx.append((c, "N"))
                codes.append(c)
                break
    dx = [(c, p if p else poa(c)) if not c.startswith(POA_EXEMPT) else (c, "") for c, p in dx]
    drg = assign_drg(dx)
    d = DRG[drg]
    n_days = los if los is not None else draw_los(person, d.gmlos * hospital_extra, d.los_min, d.los_max)
    n_days = max(d.los_min, min(d.los_max, n_days))
    discharge = day + timedelta(days=n_days)
    if newborn:
        services = [Svc("EM_NEWBORN", units=n_days, specialty="pediatrics")]
    elif surgery_key:
        services = [Svc(surgery_key, specialty=surgeon or "general_surgery")]
    else:
        services = [Svc("EM_INPT_INITIAL", specialty="hospitalist")]
        if n_days > 1:
            services.append(Svc("EM_INPT_SUBSEQ", units=n_days - 1, specialty="hospitalist"))
        services.append(Svc("EM_INPT_DISCHARGE", specialty="hospitalist"))
    spec = surgeon or ("pediatrics" if newborn else "hospitalist")
    enc = ctx.encounter(
        person, day, "inpatient", spec, dx, services, module, reason, admit=day, discharge=discharge,
        drg=drg, pcs=list(pcs), via_ed=via_ed, status_code=status, emergency=via_ed,
        readmit_of=readmit_of, prior_auth=pa,
    )
    if enc is None:
        return None
    enc.facility = ctx.directory.pick(person, "hospital", day)
    if allow_readmit and status == "01" and drg in READMIT_DRGS and readmit_of is None:
        p = ctx.cal.get("util.readmit_prob")[person.member.lob]
        if person.rng.random() < p:
            ctx.schedule(person, discharge + timedelta(days=int(person.rng.integers(3, 28))),
                         "events", "readmit", index=enc.eid, principal=principal, secondary=secondary or [])
    elif status == "01" and person.rng.random() < 0.55 and not newborn:
        visit(ctx, person, discharge + timedelta(days=int(person.rng.integers(5, 15))), module,
              reason + " (post-discharge follow-up)", principal[:1], level=4, extra=secondary or [])
    return enc


READMIT_DRGS = frozenset(
    {"291", "292", "293", "190", "191", "192", "193", "194", "195", "871", "872", "689", "690",
     "637", "638", "639", "682", "683", "684", "309", "310", "603"}
)


def icd_text(code: str) -> str:
    return ICD10CM[code].desc if code in ICD10CM else ""


def simple_service(
    ctx: SimContext, person: Person, day: date, module: str, reason: str, primary: list[str],
    specialty: str, keys: tuple[str, ...], *, setting: str = "office", units: int = 1,
    extra: list[str] | None = None,
) -> Encounter | None:
    """An encounter that bills only the named services (an eye exam, a lab, a DME supply)."""
    dx = ctx.dx_list(person, day, primary, extra, 0.5, limit=4)
    svcs = [Svc(k, units=units) for k in keys if _ok(person, k, day)]
    if not dx or not svcs:
        return None
    return ctx.encounter(person, day, setting, specialty, dx, svcs, module, reason)
