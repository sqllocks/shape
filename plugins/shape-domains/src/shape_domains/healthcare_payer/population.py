"""Members, households, plans, addresses and eligibility spans."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from importlib import resources

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]

from . import identifiers
from .calibration import Calibration, band
from .model import Member, Plan, Span
from .names import FEMALE, MALE, SURNAMES

DEFAULT_STATES = ("TX", "CA", "FL", "NY", "IL", "PA", "OH", "GA", "NC", "MI")
_STATE_WEIGHT = {"TX": 9.0, "CA": 12.0, "FL": 7.0, "NY": 6.0, "IL": 4.0, "PA": 4.0, "OH": 3.6,
                 "GA": 3.3, "NC": 3.2, "MI": 3.0}
_STREETS = ("Main", "Oak", "Maple", "Cedar", "Elm", "Washington", "Lake", "Hill", "Park", "Pine",
            "Sunset", "Church", "Highland", "Franklin", "Jefferson", "River", "Mill", "Spring")
_SUFFIX = ("St", "Ave", "Rd", "Dr", "Ln", "Blvd", "Ct", "Way")


def load_zip_reference(states: Sequence[str]) -> dict[str, list[tuple[str, str, float, float]]]:
    """state -> [(city, zip, lat, lon)] from the bundled ZIP location reference.

    The reference has no county column: ``county`` stays null until a county-bearing reference
    (GeoNames postal codes via ``shape.location.reference``) is supplied."""
    root = resources.files("shape_domains").joinpath("data/retail/reference/us_zip_locations.arrow")
    with root.open("rb") as handle:
        table = pa.ipc.open_file(handle).read_all()
    out: dict[str, list[tuple[str, str, float, float]]] = {s: [] for s in states}
    for z, city, st, lat, lon in zip(
        table["zip"].to_pylist(), table["city"].to_pylist(), table["state"].to_pylist(),
        table["lat"].to_pylist(), table["lng"].to_pylist(), strict=True,
    ):
        if st in out:
            out[st].append((city, z, lat, lon))
    missing = [s for s, rows in out.items() if not rows]
    if missing:
        raise ValueError(f"no ZIP reference rows for states: {missing}")
    return out


def build_plans() -> dict[str, Plan]:
    """Plan designs (deductibles and maxima sit below every year's regulatory limit)."""
    plans = [
        Plan("COM-PPO-1", "commercial", "PPO", "", 1500, 6000, 0.20, 30, 60, 300, 75, 0),
        Plan("COM-PPO-2", "commercial", "PPO", "", 3000, 8000, 0.30, 40, 80, 350, 90, 0),
        Plan("COM-EPO-1", "commercial", "EPO", "", 750, 5500, 0.20, 25, 50, 250, 60, 0),
        Plan("COM-HMO-1", "commercial", "HMO", "", 0, 4500, 0.10, 20, 45, 250, 50, 250, pcp_required=True),
        Plan("COM-HDHP-1", "commercial", "HDHP", "", 1650, 7000, 0.20, 0, 0, 0, 0, 0, family_multiple=2.0),
        Plan("MA-HMO-1", "ma", "MA-HMO", "", 0, 6700, 0.20, 0, 40, 90, 40, 350, pcp_required=True),
        Plan("MA-PPO-1", "ma", "MA-PPO", "", 0, 7500, 0.20, 5, 45, 90, 45, 350),
        Plan("MCD-MCO-1", "medicaid", "MCO", "", 0, 1000, 0.0, 0, 0, 0, 0, 0),
    ]
    return {p.plan_id: p for p in plans}


_COMMERCIAL_PLANS = ("COM-PPO-1", "COM-PPO-2", "COM-EPO-1", "COM-HMO-1", "COM-HDHP-1")
_COMMERCIAL_WEIGHT = (0.38, 0.12, 0.12, 0.18, 0.20)
_MA_PLANS = ("MA-HMO-1", "MA-PPO-1")


def _pick(rng: np.random.Generator, weights: dict[str, float]) -> str:
    keys = list(weights)
    return keys[int(rng.choice(len(keys), p=np.asarray(list(weights.values())) / sum(weights.values())))]


def _age_from_band(rng: np.random.Generator, weights: dict[str, float]) -> int:
    edges = {"0-17": (0, 17), "18-34": (18, 34), "35-44": (35, 44), "45-54": (45, 54),
             "55-64": (55, 64), "65-74": (65, 74), "75+": (75, 94)}
    lo, hi = edges[_pick(rng, {k: v for k, v in weights.items() if v > 0})]
    return int(rng.integers(lo, hi + 1))


def _dob(rng: np.random.Generator, ref: date, age: int) -> date:
    base = date(ref.year - age, ref.month, ref.day if ref.day < 29 else 28)
    return base - timedelta(days=int(rng.integers(0, 365)))


def _frailty(rng: np.random.Generator, shape: float) -> float:
    return float(rng.gamma(shape, 1.0 / shape))


def build_members(
    n: int, cal: Calibration, seed: int, start: date, end: date, states: Sequence[str],
    zips: dict[str, list[tuple[str, str, float, float]]], lob_mix: dict[str, float] | None = None,
) -> tuple[list[Member], dict[str, Plan]]:
    plans = build_plans()
    rng = np.random.default_rng([seed, 1])
    mix = lob_mix or cal.get("pop.lob_mix")
    female = cal.get("pop.female_share")
    shape = float(cal.get("util.frailty_shape"))
    state_w = np.asarray([_STATE_WEIGHT.get(s, 2.0) for s in states], dtype=float)
    state_w /= state_w.sum()
    members: list[Member] = []
    household = 0
    group_ids = {lob: [f"GRP{lob[:3].upper()}{i:04d}" for i in range(1, 41)] for lob in mix}

    def make(lob: str, sex: str, age: int, hh: int, rel: str, suffix: str, sub_idx: int,
             state: str, addr: tuple[str, str, str, float, float]) -> Member:
        idx = len(members)
        pool = FEMALE if sex == "F" else MALE
        first = pool[int(rng.integers(0, len(pool)))]
        last = SURNAMES[int(rng.integers(0, len(SURNAMES)))]
        street, city, zp, lat, lon = addr
        m = Member(
            idx, identifiers.member_id(idx + 1), identifiers.subscriber_id(sub_idx + 1), suffix,
            rel, first, last, sex, _dob(rng, start, age), lob, hh, state, zp, city, street, lat,
            lon, identifiers.ssn(rng), identifiers.email(first, last, idx), identifiers.phone(rng),
            frailty=_frailty(rng, shape),
        )
        members.append(m)
        return m

    sizes = {"commercial": 2.7, "ma": 1.0, "medicaid": 1.9}
    hh_mix = {k: v / sizes[k] for k, v in mix.items()}
    while len(members) < n:
        household += 1
        lob = _pick(rng, hh_mix)
        state = str(rng.choice(np.asarray(states), p=state_w))
        city, zp, lat, lon = zips[state][int(rng.integers(0, len(zips[state])))]
        street = f"{int(rng.integers(1, 9999))} {_STREETS[int(rng.integers(0, len(_STREETS)))]} {_SUFFIX[int(rng.integers(0, len(_SUFFIX)))]}"
        addr = (street, city, zp, lat + float(rng.uniform(-0.002, 0.002)), lon + float(rng.uniform(-0.002, 0.002)))
        sub_idx = len(members)
        if lob == "commercial":
            age = _age_from_band(rng, cal.get("pop.age_commercial_subscriber"))
            sex = "F" if rng.random() < female[lob] else "M"
            sub = make(lob, sex, age, household, "18", "01", sub_idx, state, addr)
            suffix = 2
            if rng.random() < cal.get("pop.spouse_prob") and age >= 22:
                s_age = max(18, min(70, age + int(rng.normal(0, 3))))
                s_sex = ("M" if sex == "F" else "F") if rng.random() < 0.94 else sex
                sp = make(lob, s_sex, s_age, household, "01", f"{suffix:02d}", sub_idx, state, addr)
                sp.last = sub.last if rng.random() < 0.7 else sp.last
                suffix += 1
            if age >= 22:
                counts = cal.get("pop.child_count")
                k = int(_pick(rng, counts))
                for _ in range(k):
                    c_age = min(max(0, age - 20 - int(rng.integers(0, 14))), 25)
                    c_age = int(rng.integers(0, 26)) if c_age > 25 else c_age
                    kid = make(lob, "F" if rng.random() < 0.49 else "M", min(c_age, 25), household,
                               "19", f"{suffix:02d}", sub_idx, state, addr)
                    kid.last = sub.last
                    suffix += 1
        elif lob == "ma":
            if rng.random() < cal.get("pop.ma_disabled_share"):
                age = int(rng.integers(40, 65))
            else:
                age = {"65-69": (65, 69), "70-74": (70, 74), "75-79": (75, 79), "80-84": (80, 84),
                       "85+": (85, 95)}[_pick(rng, cal.get("pop.ma_age"))]
                age = int(rng.integers(age[0], age[1] + 1)) if isinstance(age, tuple) else age
            sex = "F" if rng.random() < female[lob] else "M"
            make(lob, sex, age, household, "18", "01", sub_idx, state, addr)
        else:
            roll = rng.random()
            if roll < cal.get("pop.medicaid_child_share") * 0.55:  # a case with a parent and children
                p_age = int(rng.integers(20, 45))
                sub = make(lob, "F" if rng.random() < 0.8 else "M", p_age, household, "18", "01",
                           sub_idx, state, addr)
                for k in range(int(rng.integers(1, 4))):
                    kid = make(lob, "F" if rng.random() < 0.49 else "M", int(rng.integers(0, 18)),
                               household, "19", f"{k + 2:02d}", sub_idx, state, addr)
                    kid.last = sub.last
            else:
                age = _age_from_band(rng, cal.get("pop.age_medicaid_adult"))
                m = make(lob, "F" if rng.random() < female[lob] else "M", age, household, "18",
                         "01", sub_idx, state, addr)
                if age >= 65 and rng.random() < 0.6:
                    m.dual = True
    members = members[:n] if len(members) > n else members
    # plan and group per household
    by_hh: dict[int, list[Member]] = {}
    for m in members:
        by_hh.setdefault(m.household, []).append(m)
    plan_of: dict[int, str] = {}
    for hh, group in by_hh.items():
        lob = group[0].lob
        if lob == "commercial":
            plan_of[hh] = str(rng.choice(np.asarray(_COMMERCIAL_PLANS), p=np.asarray(_COMMERCIAL_WEIGHT)))
        elif lob == "ma":
            plan_of[hh] = str(rng.choice(np.asarray(_MA_PLANS), p=np.asarray([0.55, 0.45])))
        else:
            plan_of[hh] = "MCD-MCO-1"
    for hh, group in by_hh.items():
        gid = group_ids[group[0].lob][int(rng.integers(0, 40))]
        for m in group:
            m.group_id = gid
    _eligibility(members, by_hh, plan_of, cal, rng, start, end)
    return members, plans


def _span_len_years(rng: np.random.Generator) -> float:
    return float(rng.uniform(0.2, 6.0))


def _eligibility(
    members: list[Member], by_hh: dict[int, list[Member]], plan_of: dict[int, str],
    cal: Calibration, rng: np.random.Generator, start: date, end: date,
) -> None:
    term = cal.get("elig.annual_term")
    reenroll = cal.get("elig.reenroll_prob")
    gaps = cal.get("elig.gap_days")
    cob = cal.get("elig.cob_prob")
    mort = cal.get("mortality.annual")
    scale = float(cal.get("mortality.insured_scale"))
    def spans_for(lob: str, plan_id: str, new_hire: bool) -> list[Span]:
        spans: list[Span] = []
        if new_hire:
            cur = start + timedelta(days=int(rng.integers(1, max(2, (end - start).days))))
        else:
            cur = start - timedelta(days=int(_span_len_years(rng) * 365))
        plan = plan_id
        while cur <= end:
            # the span runs until a termination draw says otherwise
            years_to_term = rng.exponential(1.0 / max(term[lob], 1e-6))
            stop = cur + timedelta(days=int(years_to_term * 365.25))
            if stop >= end:
                spans.append(Span(cur, None, plan))
                break
            spans.append(Span(cur, stop, plan, "disenrolled"))
            if rng.random() >= reenroll[lob]:
                break
            lo, hi = gaps[lob]
            cur = stop + timedelta(days=int(rng.integers(lo, hi + 1)))
            if lob == "commercial" and rng.random() < 0.3:
                plan = str(rng.choice(np.asarray(_COMMERCIAL_PLANS)))
        return spans

    for hh, group in by_hh.items():
        lob = group[0].lob
        if lob == "commercial":
            spans = spans_for(lob, plan_of[hh], new_hire=bool(rng.random() < 0.15))
            for m in group:
                m.spans = [Span(s.start, s.end, s.plan_id, s.reason) for s in spans]
        else:
            for m in group:
                m.spans = spans_for(lob, plan_of[hh], new_hire=bool(rng.random() < 0.12))
        for m in group:
            m.cob = bool(rng.random() < cob[lob])
            # a dependent child ages off at 26 (ACA)
            if m.lob == "commercial" and m.relationship == "19":
                limit = date(m.dob.year + 26, m.dob.month, min(m.dob.day, 28)) - timedelta(days=1)
                clipped = []
                for s in m.spans:
                    if s.start > limit:
                        continue
                    if s.end is None or s.end > limit:
                        clipped.append(Span(s.start, limit, s.plan_id, "dependent_aged_out"))
                    else:
                        clipped.append(s)
                m.spans = clipped
            # mortality over the window
            age = m.age(start)
            years = (end - start).days / 365.25
            h = mort[band(age)] * scale * m.frailty**0.5
            if rng.random() < 1 - (1 - min(h, 0.6)) ** years:
                lo = max(start, m.spans[0].start) if m.spans else start
                m.death = lo + timedelta(days=int(rng.uniform(0, max(1, (end - lo).days))))
                for s in m.spans:
                    if s.end is None or s.end > m.death:
                        s.end = m.death if s.start <= m.death else s.end
                        s.reason = "deceased"
                m.spans = [s for s in m.spans if s.start <= m.death]
            elif m.spans and m.spans[0].start > end:
                m.spans = []


def assign_pcp(members: list[Member], directory: object, seed: int) -> None:
    """Attribute each member to a primary care provider in their state: pediatrics for children,
    family or internal medicine for adults (internal medicine leaning for 65+)."""
    rng = np.random.default_rng([seed, 11])
    by_state: dict[tuple[str, str], list[int]] = {}
    for p in directory.providers:  # type: ignore[attr-defined]
        if p.specialty in ("family_medicine", "internal_medicine", "pediatrics"):
            by_state.setdefault((p.state, p.specialty), []).append(p.idx)
    for m in members:
        age = m.age(date(m.spans[0].start.year, 1, 1)) if m.spans else 40
        if age < 16:
            order = ["pediatrics", "family_medicine"]
        elif age >= 65:
            order = ["internal_medicine", "family_medicine"]
        else:
            order = ["family_medicine", "internal_medicine"]
        spec = order[0] if rng.random() < 0.7 else order[1]
        ids = by_state.get((m.state, spec)) or by_state.get((m.state, order[1])) or []
        m.pcp = ids[int(rng.integers(0, len(ids)))] if ids else None
