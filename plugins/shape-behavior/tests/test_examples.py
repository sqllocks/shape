"""The shipped example modules behave as their documents say (recovered rates, exact delays)."""

import math
from collections import Counter, defaultdict

import numpy as np
from helpers import T0
from shape_behavior import Population, SimConfig, Simulator, load_module


def _sim(name, size, seed=1, until="2026-01-01"):
    m = load_module(name)
    sim = Simulator(
        [m], Population(size=size, start=T0, **m.doc["population_defaults"]), SimConfig(seed=seed)
    )
    events = sim.run_until(until)
    return events.to_pylist(), {r["entity_id"]: r for r in sim.entities().to_pylist()}


def _binomial_ok(k, n, p, sigmas=4.5):
    return abs(k - n * p) <= sigmas * math.sqrt(n * p * (1 - p))


def test_subscription_trial_conversion_by_plan():
    events, ents = _sim("subscription", 30_000, until="2022-01-01")
    started = {e["entity_id"] for e in events if e["kind"] == "trial_started"}
    converted = {e["entity_id"] for e in events if e["kind"] == "subscription_started"}
    lapsed = {e["entity_id"] for e in events if e["kind"] == "trial_lapsed"}
    assert converted | lapsed <= started and not converted & lapsed
    for plan, p in (("pro", 0.6), ("basic", 0.3), ("plus", 0.3)):
        members = {i for i in started if ents[i]["plan"] == plan}
        decided = members & (converted | lapsed)
        assert len(decided) > 500
        assert _binomial_ok(len(decided & converted), len(decided), p), plan


def test_subscription_invoices_are_thirty_days_apart_or_later():
    events, _ = _sim("subscription", 3000, until="2024-01-01")
    times = defaultdict(list)
    for e in events:
        if e["kind"] == "invoice_paid":
            times[e["entity_id"]].append(e["time"])
    gaps = [
        (b - a).total_seconds() / 86_400
        for ts in times.values()
        for a, b in zip(ts, ts[1:], strict=False)
    ]
    assert len(gaps) > 5000
    assert min(gaps) == 30.0
    assert Counter(round(g) for g in gaps)[30] > 0.8 * len(gaps)  # 92% renew without a pause


def test_subscription_renewal_outcomes_match_the_document():
    horizon = "2023-01-01"
    events, _ = _sim("subscription", 20_000, until=horizon)
    cutoff = np.datetime64(horizon) - np.timedelta64(31, "D")
    seq = defaultdict(list)
    for e in events:
        if e["kind"] != "loyalty_discount":
            seq[e["entity_id"]].append(e)
    outcomes = Counter()
    for evs in seq.values():
        for cur, nxt in zip(evs, evs[1:], strict=False):
            if cur["kind"] == "invoice_paid" and np.datetime64(cur["time"]) < cutoff:
                outcomes[nxt["kind"]] += 1
    n = sum(outcomes.values())
    assert n > 20_000 and set(outcomes) == {
        "invoice_paid",
        "subscription_paused",
        "subscription_cancelled",
    }
    for kind, p in (
        ("invoice_paid", 0.92),
        ("subscription_paused", 0.05),
        ("subscription_cancelled", 0.03),
    ):
        assert _binomial_ok(outcomes[kind], n, p), (kind, outcomes)


def test_equipment_breakdown_rate_depends_on_criticality_and_third_failure_replaces():
    events, ents = _sim("equipment_maintenance", 8000, until="2024-01-01")
    inspections, breakdowns = Counter(), Counter()
    per_entity = Counter()
    replaced = Counter()
    for e in events:
        crit = ents[e["entity_id"]]["criticality"]
        if e["kind"] == "observation":
            inspections[crit] += 1
        elif e["kind"] == "breakdown":
            breakdowns[crit] += 1
            per_entity[e["entity_id"]] += 1
        elif e["kind"] == "unit_replaced":
            replaced[e["entity_id"]] += 1
    assert _binomial_ok(breakdowns["high"], inspections["high"], 0.2)
    for crit in ("low", "medium"):
        assert _binomial_ok(breakdowns[crit], inspections[crit], 0.1), crit
    for eid, n in per_entity.items():
        assert replaced[eid] == n // 3


def test_equipment_inspections_follow_the_ninety_day_schedule():
    m = load_module("equipment_maintenance")
    sim = Simulator(
        [m], Population(size=500, start=T0, **m.doc["population_defaults"]), SimConfig(seed=1)
    )
    events = sim.run_until("2022-01-01").to_pylist()
    arrival = {r["entity_id"]: r["arrival"] for r in sim.entities().to_pylist()}
    first = {}
    for e in events:
        if e["kind"] == "observation":
            first.setdefault(e["entity_id"], e["time"])
    assert len(first) == 500
    for eid, when in first.items():  # the first inspection is exactly 90 days after arrival
        assert (when - arrival[eid]).total_seconds() == 90 * 86_400


def test_healthcare_example_is_clinically_ordered():
    events, ents = _sim("healthcare_screening", 20_000, until="2030-01-01")
    by = defaultdict(list)
    for e in events:
        by[e["entity_id"]].append(e)
    onsets = 0
    for eid, evs in by.items():
        kinds = [e["kind"] for e in evs]
        if "condition_onset" in kinds:
            onsets += 1
            first_onset = kinds.index("condition_onset")
            assert "medication_order" not in kinds[:first_onset]  # no drug before the diagnosis
            assert kinds.count("condition_onset") == 1  # diagnosed once
            onset = evs[first_onset]
            assert onset["code"] == "I10"
            age = (onset["time"] - ents[eid]["born"]).total_seconds() / 86_400 / 365.25
            assert age >= 18
        for e in evs:
            if e["kind"] == "encounter":
                assert (
                    e["time"] - ents[eid]["born"]
                ).total_seconds() / 86_400 / 365.25 >= 18 - 1e-6
    assert 0 < onsets < len(by)
    orders = [e for e in events if e["kind"] == "medication_order"]
    assert all(e["code"] for e in orders)
    ends = [e for e in events if e["kind"] == "medication_end"]
    assert ends and all(e["ref"] == "start_medication" for e in ends)
    assert np.isfinite([e["value"] for e in events if e["kind"] == "observation"]).all()
