"""The virtual clock and the event scheduler the clinical modules run on.

Each member has a priority queue of dated events.  The engine pops them in date order, sets the
clock and hands each to the owning module, which may emit encounters and drug courses and schedule
further events.  Members are independent, so the loop runs member by member with a random stream
keyed by ``(seed, member index)``: results never depend on how members are batched.

This is the small, domain-local runtime the modules are written against.  The behaviour lane's
domain-agnostic engine exposes the same shape (a virtual clock, dated state-machine events, emitted
records); ``behavior_engine.py`` replays that engine's events through this runtime.
"""

from __future__ import annotations

import heapq
import itertools
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Protocol

import numpy as np

from .calibration import Calibration
from .drugs import DRUGS
from .icd10cm import ICD10CM, resolve
from .model import Encounter, Member, Person, Svc, Therapy

END_OF_DAYS = date(9999, 12, 31)
FLAT = {m: 1.0 for m in range(1, 13)}
MORBIDITY_SLOPE = 1.8
# mean number of chronic conditions per member by line of business (measured on the simulated
# populations), so the multiplier keeps each line's own ED and admission rates
MEAN_CONDITIONS = {"commercial": 1.4, "ma": 3.0, "medicaid": 1.3}


class Module(Protocol):
    name: str

    def start(self, ctx: SimContext, person: Person) -> None: ...

    def handle(
        self, ctx: SimContext, person: Person, day: date, kind: str, payload: dict[str, Any]
    ) -> None: ...


@dataclass(slots=True)
class SimContext:
    cal: Calibration
    start: date
    end: date
    seed: int
    directory: Any  # ProviderDirectory (typed loosely to keep this module import-light)
    spawn_newborn: Callable[[Person, date, str], Person | None] | None = None
    modules: dict[str, Module] = field(default_factory=dict)
    today: date = date(2000, 1, 1)
    _eid: Any = field(default_factory=lambda: itertools.count(1))
    _seq: Any = field(default_factory=itertools.count)
    _queue: list[tuple[int, int, str, str, dict[str, Any]]] = field(default_factory=list)
    current: Person | None = None
    frailty_norm: float = 1.0

    # ---- scheduling -------------------------------------------------------------------------
    def schedule(self, person: Person, day: date, module: str, kind: str, **payload: Any) -> None:
        if day <= self.end and not self._dead(person, day):
            heapq.heappush(self._queue, (day.toordinal(), next(self._seq), module, kind, payload))

    @staticmethod
    def _dead(person: Person, day: date) -> bool:
        d = person.member.death
        return d is not None and day > d

    def run(self, person: Person) -> None:
        self._queue = []
        self.current = person
        for mod in self.modules.values():
            mod.start(self, person)
        while self._queue:
            ordinal, _, module, kind, payload = heapq.heappop(self._queue)
            day = date.fromordinal(ordinal)
            if self._dead(person, day):
                continue
            self.today = day
            self.modules[module].handle(self, person, day, kind, payload)

    # ---- time helpers ------------------------------------------------------------------------
    def clamp(self, day: date) -> date:
        return max(day, self.start)

    def rand_day(self, person: Person, lo: date, hi: date, weekday_bias: bool = True) -> date:
        """A random day in ``[lo, hi]``; 85% of clinic dates fall on a weekday."""
        lo, hi = max(lo, self.start), min(hi, self.end)
        if hi <= lo:
            return lo
        span = (hi - lo).days
        for _ in range(4):
            d = lo + timedelta(days=int(person.rng.integers(0, span + 1)))
            if not weekday_bias or d.weekday() < 5 or person.rng.random() < 0.15:
                return d
        return d

    def exp_next(self, person: Person, mean_days: float, after: date, floor: int = 14) -> date:
        gap = max(floor, int(person.rng.exponential(mean_days)))
        return after + timedelta(days=gap)

    def morbidity(self, person: Person) -> float:
        """Acute-care multiplier from the member's number of chronic conditions (mean about 1):
        admissions and ED visits concentrate in the sick (AHRQ MEPS / HCUP: the top decile by
        comorbidity burden carries about half of admissions)."""
        n = sum(1 for k in person.conds if not k.startswith("cancer"))
        mean = MEAN_CONDITIONS[person.member.lob]
        return (1.0 + MORBIDITY_SLOPE * n) / (1.0 + MORBIDITY_SLOPE * mean)

    def seasonal_dates(
        self,
        person: Person,
        annual_rate: float,
        weights: dict[int, float],
        lo: date,
        hi: date,
        morbid: bool = False,
    ) -> list[date]:
        """Poisson event dates between lo and hi, month-weighted (mean weight 1 over a year)."""
        norm = sum(weights.values()) / 12.0
        if morbid:
            annual_rate *= self.morbidity(person)
        out: list[date] = []
        cur = date(lo.year, lo.month, 1)
        while cur <= hi:
            nxt = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
            a, b = max(cur, lo), min(nxt - timedelta(days=1), hi)
            if b >= a:
                frac = ((b - a).days + 1) / 365.25
                lam = (
                    annual_rate
                    * weights[cur.month]
                    / norm
                    * frac
                    * person.member.frailty**0.5
                    / self.frailty_norm
                )
                for _ in range(int(person.rng.poisson(lam))):
                    out.append(self.rand_day(person, a, b))
            cur = nxt
        out.sort()
        return out

    def year_ends(self) -> list[tuple[date, date]]:
        out = []
        for y in range(self.start.year, self.end.year + 1):
            out.append((max(date(y, 1, 1), self.start), min(date(y, 12, 31), self.end)))
        return out

    # ---- coding helpers ----------------------------------------------------------------------
    def code(self, person: Person, concept: str, day: date) -> str | None:
        """Resolve a concept to a code valid on ``day`` and for this member's age and sex."""
        code = resolve(concept, day)
        icd = ICD10CM.get(code)
        if icd is None or not icd.billable or not icd.valid_on(day):
            return None
        m = person.member
        age = m.age(day)
        if icd.sex and icd.sex != m.sex:
            return None
        if not icd.age_min <= age <= icd.age_max:
            return None
        return code

    def dx_list(
        self,
        person: Person,
        day: date,
        primary: list[str],
        extra: list[str] | None = None,
        capture: float = 0.65,
        limit: int = 6,
    ) -> list[tuple[str, str]]:
        """Ordered diagnosis codes: the reason first, then problem-list codes that get coded."""
        seen: list[str] = []
        for concept in primary:
            c = self.code(person, concept, day)
            if c and c not in seen:
                seen.append(c)
        for concept in extra or []:
            if len(seen) >= limit:
                break
            if person.rng.random() < capture:
                c = self.code(person, concept, day)
                if c and c not in seen:
                    seen.append(c)
        return [(c, "") for c in seen[:limit]]

    # ---- emitting ----------------------------------------------------------------------------
    def encounter(
        self,
        person: Person,
        day: date,
        setting: str,
        specialty: str,
        dx: list[tuple[str, str]],
        services: list[Svc],
        module: str,
        reason: str,
        **kw: Any,
    ) -> Encounter | None:
        if not dx:
            return None
        if day < self.start or day > self.end:
            return None
        provider = kw.pop("provider", None)
        if provider is None:
            provider = self.directory.pick(person, specialty, day)
        enc = Encounter(
            next(self._eid),
            person.member.idx,
            day,
            setting,
            specialty,
            dx,
            services,
            module,
            reason,
            provider=provider,
            **kw,
        )
        person.encounters.append(enc)
        return enc

    def prescribe(
        self,
        person: Person,
        day: date,
        drug_key: str,
        indication: str,
        dx: str,
        specialty: str,
        days_supply: int = 30,
        refills: int = 5,
        acute: bool = False,
        encounter: Encounter | None = None,
        stop: date | None = None,
        quantity: float | None = None,
    ) -> Therapy | None:
        drug = DRUGS[drug_key]
        m = person.member
        if day > self.end or drug.launch > day:
            return None
        if (drug.sex and drug.sex != m.sex) or not drug.age_min <= m.age(day) <= drug.age_max:
            return None
        for t in person.therapies:
            if t.drug_key == drug_key and t.stop is None and not t.acute and not acute:
                return t  # already on it
        dx = resolve(dx, day)
        daw = "0" if person.rng.random() < 0.97 else "1"
        t = Therapy(
            drug_key,
            day,
            stop,
            indication,
            dx,
            specialty,
            days_supply,
            refills,
            daw,
            acute,
            encounter.eid if encounter else None,
            quantity,
        )
        person.therapies.append(t)
        return t

    def stop_drug(self, person: Person, drug_key: str, day: date) -> None:
        for t in person.therapies:
            if t.drug_key == drug_key and t.stop is None:
                t.stop = day

    def on_drug_class(self, person: Person, keys: tuple[str, ...], day: date) -> bool:
        return any(
            t.drug_key in keys and t.start <= day and (t.stop is None or t.stop >= day)
            for t in person.therapies
        )

    def poisson_days(
        self, person: Person, rate: float, lo: date, hi: date, morbid: bool = False
    ) -> list[date]:
        return self.seasonal_dates(person, rate, FLAT, lo, hi, morbid)

    def kill(self, person: Person, day: date) -> None:
        """End the member's life (and coverage) on ``day``."""
        m = person.member
        if m.death is not None and m.death <= day:
            return
        m.death = day
        for s in m.spans:
            if s.end is None or s.end > day:
                s.end = day
                s.reason = "deceased"
        m.spans = [s for s in m.spans if s.start <= day]


def person_rng(seed: int, idx: int) -> np.random.Generator:
    return np.random.Generator(np.random.PCG64(np.random.SeedSequence([seed, idx])))


def new_person(member: Member, seed: int) -> Person:
    return Person(member=member, rng=person_rng(seed, member.idx))


def add_days(day: date, n: int) -> date:
    return day + timedelta(days=int(n))
