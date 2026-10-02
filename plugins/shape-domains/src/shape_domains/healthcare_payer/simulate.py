"""Run the clinical simulation for a population and return the per-member records."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from . import identifiers
from .calibration import Calibration
from .care_events import EVENT_MODULES
from .chronic import CHRONIC_MODULES
from .cohort import CohortModule
from .directory import ProviderDirectory
from .engine import SimContext, new_person
from .model import Member, Person, Plan, Span
from .population import DEFAULT_STATES, assign_pcp, build_members, load_zip_reference


@dataclass(slots=True)
class Simulation:
    members: list[Member]
    persons: list[Person]
    plans: dict[str, Plan]
    directory: ProviderDirectory
    cal: Calibration
    start: date
    end: date
    seed: int
    stats: dict[str, int] = field(default_factory=dict)


def simulate(
    n_members: int = 2000, *, seed: int = 42, start: date = date(2022, 1, 1),
    end: date = date(2024, 12, 31), states: tuple[str, ...] = DEFAULT_STATES,
    lob_mix: dict[str, float] | None = None, calibration: Calibration | None = None,
) -> Simulation:
    cal = calibration or Calibration()
    zips = load_zip_reference(states)
    members, plans = build_members(n_members, cal, seed, start, end, states, zips, lob_mix)
    state_counts: dict[str, int] = {}
    for m in members:
        state_counts[m.state] = state_counts.get(m.state, 0) + 1
    directory = ProviderDirectory(state_counts, zips, cal, seed)
    assign_pcp(members, directory, seed)

    persons: list[Person] = []
    pending: list[Person] = []
    next_suffix: dict[int, int] = {}
    for m in members:
        next_suffix[m.household] = max(next_suffix.get(m.household, 0), int(m.suffix))
    rng_newborn = np.random.default_rng([seed, 31])

    def spawn(mother: Person, day: date, delivery: str) -> Person | None:
        mm = mother.member
        if mm.lob == "ma":
            return None
        span = mm.span_on(day)
        if span is None:
            return None  # a mother without coverage on the day: the baby is not enrolled
        idx = len(members)
        suffix = next_suffix[mm.household] = next_suffix[mm.household] + 1
        sex = "F" if rng_newborn.random() < 0.49 else "M"
        from .names import FEMALE, MALE
        pool = FEMALE if sex == "F" else MALE
        first = pool[int(rng_newborn.integers(0, len(pool)))]
        baby = Member(
            idx, identifiers.member_id(idx + 1), mm.subscriber_id, f"{suffix:02d}", "19", first, mm.last,
            sex, day, mm.lob, mm.household, mm.state, mm.zip, mm.city, mm.street, mm.lat, mm.lon,
            identifiers.ssn(rng_newborn), identifiers.email(first, mm.last, idx), mm.phone,
            spans=[Span(day, span.end, span.plan_id, span.reason)], frailty=1.0, group_id=mm.group_id,
        )
        baby.cob = False
        members.append(baby)
        ids = [p.idx for p in directory.providers if p.specialty == "pediatrics" and p.state == mm.state]
        baby.pcp = ids[int(rng_newborn.integers(0, len(ids)))] if ids else None
        person = new_person(baby, seed)
        pending.append(person)
        return person

    ctx = SimContext(cal, start, end, seed, directory, spawn_newborn=spawn)
    k = float(cal.get("util.frailty_shape"))
    # E[frailty ** 0.5] for a gamma(k, 1/k) frailty, so the mean utilisation does not depend on k
    ctx.frailty_norm = math.gamma(k + 0.5) / (math.gamma(k) * math.sqrt(k))
    mods = [CohortModule()] + [c() for c in CHRONIC_MODULES] + [c() for c in EVENT_MODULES]
    ctx.modules = {m.name: m for m in mods}
    # the cohort assigns conditions first; modules then see them in start()
    for m in list(members):
        person = new_person(m, seed)
        persons.append(person)
        ctx.run(person)
        while pending:
            baby = pending.pop(0)
            persons.append(baby)
            ctx.run(baby)
    stats = {"members": len(members), "encounters": sum(len(p.encounters) for p in persons),
             "therapies": sum(len(p.therapies) for p in persons)}
    return Simulation(members, persons, plans, directory, cal, start, end, seed, stats)
