"""Run the diabetes, hypertension and lipid pathways on the behavior engine.

``simulate(..., engine="behavior")`` replaces the native ``diabetes``, ``htn`` and ``lipid`` modules
with the documents of ``behavior_modules`` (format ``shape-behavior/1``).  The flow:

1. **Profile.**  The native cohort runs once per member to find who has diabetes, hypertension,
   a lipid disorder and obesity on day one (the same clustered draw the native engine makes), and
   the result is written into the behavior entities' attributes.
2. **Simulate.**  ``shape_behavior.Simulator`` runs the documents for all members at once and
   returns the event table (``condition_onset``, ``medication_order``, ``visit``, ``exam``).
3. **Replay.**  ``BehaviorPathways`` schedules each event on the member's own clock, and the
   native clinical helpers turn it into an encounter or a drug course, so claims, pharmacy fills,
   cost sharing and the quality checks are the same code for both engines.

What the documents do not model (and the native modules do): diabetes complications, hypoglycaemia
and ketoacidosis admissions, supplies, and coverage-aware review chains.  They are listed in the
domain page.
"""

from __future__ import annotations

import importlib.util
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np

from .behavior_modules import LIPID_CODES, build_modules
from .calibration import Calibration, band
from .chronic import _start_drug
from .clinical import pcp_specialty, simple_service, visit
from .cohort import CohortModule
from .engine import SimContext, new_person
from .model import Member, Person
from .problem import dm_codes, htn_code, problem_codes

KEYS = frozenset({"dm", "htn", "lipid"})
NATIVE_REPLACED = ("diabetes", "htn", "lipid")
REASON = {"dm": "diabetes review", "htn": "hypertension review", "lipid": "lipid follow-up"}
_KEY_OF_INDICATION = {"dm1": "dm", "dm2": "dm", "htn": "htn", "lipid": "lipid"}


def available() -> bool:
    return importlib.util.find_spec("shape_behavior") is not None


def profiles(
    members: list[Member], cal: Calibration, seed: int, start: date, end: date
) -> dict[int, dict[str, Any]]:
    """Day-one profile of each member from the clustered cohort (obesity, dm, htn, lipid)."""
    ctx = SimContext(cal, start, end, seed, None)
    cohort = CohortModule(KEYS)
    out: dict[int, dict[str, Any]] = {}
    for m in members:
        person = new_person(m, seed)
        ctx._queue = []
        cohort.start(ctx, person)
        dm = person.conds.get("dm")
        lipid = person.conds.get("lipid")
        out[m.idx] = {
            "age_band": band(m.age(start)),
            "obese": 1 if person.has("obesity") else 0,
            "dm_start": ("t1" if dm.data.get("type") == "E10" else "t2") if dm else "none",
            "htn_start": 1 if person.has("htn") else 0,
            "lipid_start": (lipid.code if lipid and lipid.code in LIPID_CODES else LIPID_CODES[0])
            if lipid
            else "none",
        }
    return out


def run_events(
    members: list[Member], cal: Calibration, seed: int, start: date, end: date
) -> dict[int, list[dict[str, Any]]]:
    """The behavior engine's events for the pathways, grouped by member index."""
    from shape_behavior.population import Population  # type: ignore[import-untyped,unused-ignore]
    from shape_behavior.simulator import (  # type: ignore[import-untyped,unused-ignore]
        SimConfig,
        Simulator,
    )

    n = len(members)
    prof = profiles(members, cal, seed, start, end)
    pop = Population(size=n, start=start.isoformat(), first_id=0)
    sim = Simulator(build_modules(cal), pop, SimConfig(seed=seed))
    rows = np.arange(n)
    for name in ("age_band", "obese", "dm_start", "htn_start", "lipid_start"):
        sim.store.write(name, rows, [prof[m.idx][name] for m in members])
    table = sim.run_until((end + timedelta(days=1)).isoformat())
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in table.to_pylist():
        if r["kind"] in ("condition_onset", "medication_order", "visit", "exam"):
            out[int(r["entity_id"])].append(r)
    for events in out.values():
        events.sort(key=lambda e: (e["time"], e["seq"]))
    return out


def _day(t: datetime) -> date:
    return t.date()


def _key_of_code(code: str) -> str | None:
    if code.startswith(("E10", "E11")):
        return "dm"
    if code.startswith("I10"):
        return "htn"
    if code.startswith("E78"):
        return "lipid"
    return None


def _primary(person: Person, keys: list[str]) -> list[str]:
    out: list[str] = []
    for k in keys:
        if not person.has(k):
            continue
        if k == "dm":
            out.append(dm_codes(person)[0])
        elif k == "htn":
            out.append(htn_code(person))
        elif k == "lipid":
            out.append(person.conds["lipid"].code)
    return out


class BehaviorPathways:
    """Replays the behavior engine's events for one member through the native clinical helpers."""

    name = "behavior"

    def __init__(self, events: dict[int, list[dict[str, Any]]]) -> None:
        self.events = events

    def start(self, ctx: SimContext, person: Person) -> None:
        prevalent = {k for k in KEYS if person.has(k)}
        for key in sorted(prevalent):
            self._coverage_visits(ctx, person, key, ctx.start)
        for ev in self.events.get(person.member.idx, []):
            day = ctx.clamp(_day(ev["time"]))
            ctx.schedule(person, day, self.name, ev["kind"], ev=ev, prevalent=sorted(prevalent))

    def handle(
        self, ctx: SimContext, person: Person, day: date, kind: str, payload: dict[str, Any]
    ) -> None:
        if kind == "coverage_visit":
            self.handle_coverage_visit(ctx, person, day, payload["key"])
            return
        getattr(self, f"on_{kind}")(ctx, person, day, payload["ev"], set(payload["prevalent"]))

    # ---- events ------------------------------------------------------------------------------
    def _coverage_visits(self, ctx: SimContext, person: Person, key: str, since: date) -> None:
        """A visit soon after the condition is known, inside each coverage span, as a member who
        enrols with a chronic condition establishes care (so every fill has a coded diagnosis)."""
        for s in person.member.spans:
            begin = max(s.start, since, ctx.start)
            stop = min(s.end or ctx.end, ctx.end)
            if begin > stop:
                continue
            hi = min(begin + timedelta(days=120), stop)
            day = ctx.rand_day(person, begin, hi, weekday_bias=False)
            ctx.schedule(person, day, self.name, "coverage_visit", key=key)

    def handle_coverage_visit(self, ctx: SimContext, person: Person, day: date, key: str) -> None:
        primary = _primary(person, [key])
        if primary:
            visit(
                ctx,
                person,
                day,
                key,
                REASON[key],
                primary,
                extra=problem_codes(person, day),
            )

    def on_condition_onset(
        self, ctx: SimContext, person: Person, day: date, ev: dict[str, Any], prevalent: set[str]
    ) -> None:
        key = _key_of_code(ev["code"])
        if key is None or person.has(key):
            return  # a day-one condition the cohort already assigned
        cohort = CohortModule(KEYS)
        cohort._assign(ctx, person, key, day, incident=True)
        cond = person.conds[key]
        if key == "dm":
            cond.data["type"] = ev["code"][:3]
        elif key == "lipid":
            cond.code = ev["code"]
        reasons = {
            "dm": ("new diabetes diagnosis", ("LAB_HBA1C", "LAB_CMP", "LAB_LIPID_PANEL")),
            "htn": ("new hypertension diagnosis", ("LAB_BMP", "LAB_LIPID_PANEL")),
            "lipid": ("new lipid disorder diagnosis", ("LAB_LIPID_PANEL",)),
        }
        reason, labs = reasons[key]
        visit(
            ctx,
            person,
            day,
            key,
            reason,
            _primary(person, [key]),
            new=True,
            level=4 if key == "dm" else 3,
            specialty=pcp_specialty(ctx, person),
            labs=labs,
            extra=problem_codes(person, day),
        )
        self._coverage_visits(ctx, person, key, day)

    def on_medication_order(
        self, ctx: SimContext, person: Person, day: date, ev: dict[str, Any], prevalent: set[str]
    ) -> None:
        indication = ev["display"] or ""
        key = _KEY_OF_INDICATION.get(indication)
        if key is None or not person.has(key):
            return
        dx = _primary(person, [key])
        spec = "endocrinology" if indication == "dm1" else pcp_specialty(ctx, person)
        _start_drug(ctx, person, day, key not in prevalent, ev["code"], indication, dx[0], spec)

    def on_visit(
        self, ctx: SimContext, person: Person, day: date, ev: dict[str, Any], prevalent: set[str]
    ) -> None:
        p = json.loads(ev["payload"])
        primary = _primary(person, p["dx"])
        if not primary:
            return
        spec = None if p["specialty"] == "pcp" else p["specialty"]
        visit(
            ctx,
            person,
            day,
            p["module"],
            p["reason"],
            primary,
            specialty=spec,
            labs=tuple(p.get("labs", ())),
            extra=problem_codes(person, day),
        )

    def on_exam(
        self, ctx: SimContext, person: Person, day: date, ev: dict[str, Any], prevalent: set[str]
    ) -> None:
        p = json.loads(ev["payload"])
        primary = _primary(person, p["dx"])
        if not primary:
            return
        spec, setting = p["specialty"], "office"
        if spec == "pcp":
            spec = pcp_specialty(ctx, person)
        elif spec == "lab":
            spec, setting = "laboratory", "lab"
        elif spec == "eye":
            spec = "ophthalmology" if person.rng.random() < 0.45 else "optometry"
        simple_service(
            ctx,
            person,
            day,
            p["module"],
            p["reason"],
            primary,
            spec,
            (p["service"],),
            setting=setting,
        )
