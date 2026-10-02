"""The cohort module: who has which chronic condition, with clustering, and who develops one.

Prevalence by age band comes from the calibration table.  Comorbidity clustering is applied as
odds ratios on top of the age prevalence (obesity raises hypertension and diabetes; diabetes and
hypertension raise kidney disease and heart disease) and the base odds are solved so each
condition's *marginal* prevalence by band still matches the published figure.  A member who does
not have a condition at the start of the window may develop one: the yearly hazard is the slope of
the prevalence curve with age, scaled by the member's risk factors.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from functools import lru_cache
from itertools import product
from typing import Any

from .calibration import BANDS, band
from .engine import SimContext
from .model import Cond, Person

ORDER = (
    "obesity", "dm", "htn", "lipid", "ckd", "cad", "hf", "afib", "asthma", "copd", "depression",
    "anxiety", "adhd", "sud_opioid", "sud_alcohol", "bipolar", "schizophrenia", "hypothyroid",
    "gerd", "osteoporosis", "bph", "autoimmune",
)
# condition -> [(parent condition, odds-ratio key table, key)]
PARENTS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "dm": (("obesity", "cluster.obesity_or", "dm"),),
    "htn": (("obesity", "cluster.obesity_or", "htn"), ("dm", "cluster.dm_or", "htn")),
    "lipid": (("obesity", "cluster.obesity_or", "lipid"), ("dm", "cluster.dm_or", "lipid")),
    "ckd": (("dm", "cluster.dm_or", "ckd"), ("htn", "cluster.htn_or", "ckd"),
            ("obesity", "cluster.obesity_or", "ckd")),
    "cad": (("dm", "cluster.dm_or", "cad"), ("htn", "cluster.htn_or", "cad")),
    "hf": (("dm", "cluster.dm_or", "hf"), ("htn", "cluster.htn_or", "hf")),
    "afib": (("htn", "cluster.htn_or", "afib"),),
    "asthma": (("obesity", "cluster.obesity_or", "asthma"),),
    "depression": (("obesity", "cluster.obesity_or", "depression"), ("dm", "cluster.dm_or", "depression")),
    "anxiety": (("depression", "", ""),),
}
_ANXIETY_GIVEN_DEPRESSION_OR = 4.0
FLOOR = {"htn": 0.004, "dm": 0.003, "lipid": 0.006, "ckd": 0.002, "asthma": 0.0015,
         "depression": 0.006, "anxiety": 0.006, "adhd": 0.002, "copd": 0.002, "hf": 0.002,
         "cad": 0.003, "afib": 0.002, "obesity": 0.0, "hypothyroid": 0.004, "gerd": 0.006,
         "autoimmune": 0.0008, "sud_opioid": 0.0005, "sud_alcohol": 0.0008, "bipolar": 0.0003, "schizophrenia": 0.0002,
         "osteoporosis": 0.003, "bph": 0.004}
_CENTERS = (9, 26, 40, 50, 60, 70, 80)
_SCALAR = {"bipolar": "cond.bipolar", "schizophrenia": "cond.schizophrenia", "gerd": "cond.gerd",
           "autoimmune": "cond.autoimmune"}


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _logit(p: float) -> float:
    p = min(max(p, 1e-9), 1 - 1e-9)
    return math.log(p / (1 - p))


@lru_cache(maxsize=4096)
def solve_base(target: float, factors: tuple[tuple[float, float], ...]) -> float:
    """Base logit b with E[sigmoid(b + sum f_i ln OR_i)] = target, f_i ~ Bernoulli(q_i)."""
    if target <= 0:
        return -30.0
    lo, hi = -30.0, 8.0
    for _ in range(60):
        mid = (lo + hi) / 2
        total = 0.0
        for combo in product((0, 1), repeat=len(factors)):
            w = 1.0
            shift = 0.0
            for f, (q, orr) in zip(combo, factors, strict=True):
                w *= q if f else 1 - q
                shift += f * math.log(orr)
            total += w * _sigmoid(mid + shift)
        lo, hi = (mid, hi) if total < target else (lo, mid)
    return (lo + hi) / 2


class Prevalence:
    """Marginal prevalence by band, age interpolation for incidence."""

    def __init__(self, ctx: SimContext) -> None:
        self.ctx = ctx
        self.cal = ctx.cal

    def p(self, key: str, age: int, sex: str) -> float:
        cal = self.cal
        if key == "hypothyroid":
            return float(cal.get("cond.hypothyroid")[sex]) if age >= 18 else 0.0
        if key in _SCALAR:
            return float(cal.get(_SCALAR[key])) if age >= (15 if key != "gerd" else 18) else 0.0
        if key == "osteoporosis":
            return float(cal.get("cond.osteoporosis_f")[band(age)]) if sex == "F" else 0.0
        if key == "bph":
            return float(cal.get("cond.bph_m")[band(age)]) if sex == "M" else 0.0
        table = cal.get(f"cond.{key}")
        value = float(table[band(age)])
        if key == "adhd":
            return value / 0.83 if 3 <= age <= 17 else value if age >= 18 else 0.0
        if key in ("depression",) and age < 12:
            return 0.0
        if key == "sud_opioid" and age < 12:
            return 0.0
        return value

    def incidence(self, key: str, age: int, sex: str) -> float:
        """Yearly hazard from the slope of the prevalence curve, never below a floor."""
        p0 = self.p(key, age, sex)
        p1 = self.p(key, age + 5, sex)
        if p0 <= 0 and p1 <= 0:
            return 0.0  # outside the ages the condition occurs at
        h = max((p1 - p0) / 5.0 / max(1e-6, 1 - p0), FLOOR.get(key, 0.001))
        if age < 18 and key in ("htn", "dm", "lipid", "ckd", "cad", "hf", "afib", "copd"):
            return 0.0 if key != "dm" else 0.0003
        return h


class CohortModule:
    name = "cohort"

    def start(self, ctx: SimContext, person: Person) -> None:
        prev = Prevalence(ctx)
        m = person.member
        age = m.age(ctx.start)
        rng = person.rng
        flags: set[str] = set()
        for key in ORDER:
            target = prev.p(key, age, m.sex)
            if target <= 0:
                continue
            if key == "ckd" and not (person.has("dm") or person.has("htn")):
                target *= 0.35  # kidney disease without its main causes is rarer
            parents = self._parents(ctx, prev, key, age, m.sex, flags)
            base = solve_base(round(target, 6), tuple(parents[0]))
            shift = sum(math.log(orr) for on, (_, orr) in zip(parents[1], parents[0], strict=True) if on)
            if rng.random() < _sigmoid(base + shift):
                flags.add(key)
                self._assign(ctx, person, key, ctx.start - timedelta(days=int(rng.integers(30, 3650))))
        # incident conditions during the window
        years = max(0.0, (ctx.end - ctx.start).days / 365.25)
        for key in ORDER:
            if person.has(key) or key == "obesity":
                continue
            h = prev.incidence(key, age, m.sex)
            if key == "dm" and person.has("obesity"):
                h *= 2.5
            if key in ("htn", "lipid") and person.has("obesity"):
                h *= 1.6
            if key == "ckd" and (person.has("dm") or person.has("htn")):
                h *= 3.0
            if h <= 0:
                continue
            t = rng.exponential(1.0 / h)
            if t < years:
                ctx.schedule(person, ctx.start + timedelta(days=int(t * 365.25) + 1), self.name,
                             "incident", key=key)

    def _parents(self, ctx: SimContext, prev: Prevalence, key: str, age: int, sex: str,
                 flags: set[str]) -> tuple[list[tuple[float, float]], list[bool]]:
        out: list[tuple[float, float]] = []
        on: list[bool] = []
        for parent, table, k in PARENTS.get(key, ()):
            if table:
                orr = float(ctx.cal.get(table)[k])
            else:
                orr = _ANXIETY_GIVEN_DEPRESSION_OR
            out.append((max(prev.p(parent, age, sex), 1e-6), orr))
            on.append(parent in flags)
        return out, on

    def handle(self, ctx: SimContext, person: Person, day: date, kind: str, payload: dict[str, Any]) -> None:
        key = payload["key"]
        if kind != "incident" or person.has(key):
            return
        self._assign(ctx, person, key, day, incident=True)
        module = ctx.modules.get(key if key in ctx.modules else CONDITION_MODULE.get(key, ""))
        if module is not None and hasattr(module, "onboard"):
            module.onboard(ctx, person, day, True, key)

    # ---- condition details ---------------------------------------------------------------------
    def _assign(self, ctx: SimContext, person: Person, key: str, onset: date, incident: bool = False) -> None:
        rng = person.rng
        m = person.member
        age = m.age(onset)
        cond = Cond(key, onset)
        data = cond.data
        if key == "obesity":
            data["coded"] = bool(rng.random() < ctx.cal.get("cond.obesity_coded"))
            r = rng.random()
            data["cls"] = "obesity_class1" if r < 0.55 else "obesity_class2" if r < 0.85 else "obesity_class3"
            data["z68"] = bool(rng.random() < 0.35)
        elif key == "dm":
            share = ctx.cal.get("cond.dm1_share")
            t1 = rng.random() < (share["child"] if age < 18 else share["adult"] * (0.3 if age >= 45 else 1.0))
            data["type"] = "E10" if t1 else "E11"
            comp = []
            if not incident:
                for k, p in (("neuropathy", 0.10 + 0.002 * max(0, age - 40)),
                             ("retinopathy", 0.06 + 0.002 * max(0, age - 40)),
                             ("angiopathy", 0.03), ("ulcer", 0.015)):
                    if rng.random() < p:
                        comp.append(k)
            data["complications"] = comp
            data["hyperglycemia"] = bool(rng.random() < (0.05 if incident else 0.20))
        elif key == "lipid":
            r = rng.random()
            cond.code = "E78.5" if r < 0.58 else "E78.2" if r < 0.80 else "E78.00"
        elif key == "ckd":
            diabetic = person.has("dm")
            probs = [0.15, 0.22, 0.38, 0.18, 0.05, 0.02] if age < 65 else [0.08, 0.17, 0.48, 0.20, 0.05, 0.02]
            stage = int(rng.choice([1, 2, 3, 4, 5, 6], p=probs))
            if incident:
                stage = int(rng.choice([1, 2, 3], p=[0.25, 0.4, 0.35]))
            cond.stage = stage
            data["sub"] = ["a", "b", ""][int(rng.choice(3, p=[0.45, 0.35, 0.20]))] if stage == 3 else ""
            data["diabetic"] = diabetic
        elif key == "asthma":
            r = rng.random()
            cond.code = "J45.909" if r < 0.45 else "J45.20" if r < 0.60 else "J45.30" if r < 0.80 else "J45.40"
        elif key == "copd":
            data["smoker"] = bool(rng.random() < 0.45)
        elif key == "depression":
            cond.code = "depression_unsp" if rng.random() < 0.6 else "F33.1"
        elif key == "anxiety":
            cond.code = "F41.1" if rng.random() < 0.5 else "F41.9"
        elif key == "adhd":
            cond.code = "F90.9" if rng.random() < 0.6 else "F90.2"
        elif key == "hf":
            data["systolic"] = bool(rng.random() < 0.5)
        elif key == "autoimmune":
            r = rng.random()
            cond.code, data["specialty"] = (("M06.9", "rheumatology") if r < 0.40 else ("L40.0", "dermatology") if r < 0.65 else ("K50.90", "gastroenterology") if r < 0.85 else ("K51.90", "gastroenterology"))
        person.conds[key] = cond


# condition key -> the module that manages it
CONDITION_MODULE: dict[str, str] = {
    "dm": "diabetes", "htn": "htn", "lipid": "lipid", "ckd": "ckd", "asthma": "resp_chronic",
    "copd": "resp_chronic", "depression": "behavioral", "anxiety": "behavioral",
    "adhd": "behavioral", "sud_opioid": "behavioral", "sud_alcohol": "behavioral",
    "bipolar": "behavioral", "schizophrenia": "behavioral", "hypothyroid": "simple",
    "gerd": "simple", "osteoporosis": "simple", "bph": "simple", "autoimmune": "simple", "cad": "cardiac", "hf": "cardiac",
    "afib": "cardiac", "obesity": "",
}
assert set(BANDS)  # the band names are shared with the calibration table
