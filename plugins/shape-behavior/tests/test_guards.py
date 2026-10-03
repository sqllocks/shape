"""Guards: an age guard is waited for exactly; any other guard is polled on the virtual clock."""

import numpy as np
from helpers import END, T0, counts, module, start
from shape_behavior import Population, SimConfig, Simulator

DAY = 86_400 * 1_000_000


def _us(col) -> np.ndarray:
    return (
        np.asarray(col.cast("timestamp[us]").to_numpy(zero_copy_only=False))
        .astype("datetime64[us]")
        .astype(np.int64)
    )


def test_age_guard_releases_exactly_at_the_birthday():
    m = module(
        {
            "s": start("g"),
            "g": {
                "type": "guard",
                "condition": {"type": "age", "op": ">=", "value": 18, "unit": "years"},
                "transition": {"direct": "adult"},
            },
            "adult": {"type": "event", "event": "came_of_age", "transition": {"direct": "end"}},
            "end": END,
        }
    )
    sim = Simulator(
        [m],
        Population(size=500, start=T0, age_at_start={"kind": "uniform", "low": 0, "high": 17}),
        SimConfig(seed=1),
    )
    t = sim.run_until("2040-01-01")
    ents = sim.entities()
    born = dict(zip(ents.column("entity_id").to_pylist(), _us(ents.column("born")), strict=True))
    assert counts(t, "kind") == {"came_of_age": 500}
    for eid, when in zip(t.column("entity_id").to_pylist(), _us(t.column("time")), strict=True):
        years = (when - born[eid]) / DAY / 365.25
        assert abs(years - 18) < 1e-6, years


def test_an_already_adult_entity_passes_straight_through():
    m = module(
        {
            "s": start("g"),
            "g": {
                "type": "guard",
                "condition": {"type": "age", "op": ">=", "value": 18, "unit": "years"},
                "transition": {"direct": "adult"},
            },
            "adult": {"type": "event", "event": "came_of_age", "transition": {"direct": "end"}},
            "end": END,
        }
    )
    sim = Simulator(
        [m],
        Population(size=100, start=T0, age_at_start={"kind": "uniform", "low": 30, "high": 60}),
        SimConfig(seed=1),
    )
    t = sim.run_until("2020-01-02")
    assert set(_us(t.column("time"))) == {int(np.datetime64(T0, "us").astype(np.int64))}


def test_attribute_guard_is_polled_and_never_releases_early():
    # "ready" turns on 10 days in; the guard polls every 7 days, so it releases at day 14.
    m = module(
        {
            "s": start("wait"),
            "wait": {
                "type": "delay",
                "delay": {"kind": "exact", "value": 10, "unit": "days"},
                "transition": {"direct": "flip"},
            },
            "flip": {
                "type": "set_attribute",
                "attribute": "ready",
                "value": 1,
                "transition": {"direct": "end"},
            },
            "end": END,
        },
        name="flipper",
        attributes={"ready": {"kind": "constant", "value": 0}},
    )
    watcher = module(
        {
            "s": start("g"),
            "g": {
                "type": "guard",
                "condition": {"type": "attribute", "attribute": "ready", "op": "==", "value": 1},
                "transition": {"direct": "go"},
            },
            "go": {"type": "event", "event": "released", "transition": {"direct": "end"}},
            "end": END,
        },
        name="watcher",
        attributes={"ready": {"kind": "constant", "value": 0}},
    )
    sim = Simulator([m, watcher], Population(size=50, start=T0), SimConfig(seed=1, poll="7 days"))
    t = sim.run_until("2021-01-01")
    released = t.filter(
        __import__("pyarrow.compute", fromlist=["x"]).equal(t.column("kind"), "released")
    )
    start_us = int(np.datetime64(T0, "us").astype(np.int64))
    times = (_us(released.column("time")) - start_us) / DAY
    assert released.num_rows == 50
    assert times.min() >= 10 and times.max() <= 10 + 7 + 1e-9
    assert set(np.round(times, 6)) == {14.0}


def test_a_guard_that_never_holds_never_releases():
    m = module(
        {
            "s": start("g"),
            "g": {
                "type": "guard",
                "condition": {"type": "attribute", "attribute": "x", "op": ">", "value": 5},
                "transition": {"direct": "go"},
            },
            "go": {"type": "event", "event": "released", "transition": {"direct": "end"}},
            "end": END,
        },
        attributes={"x": {"kind": "constant", "value": 1}},
    )
    sim = Simulator([m], Population(size=100, start=T0), SimConfig(seed=1))
    assert sim.run_until("2030-01-01").num_rows == 0


def test_conditions_and_or_not_on_attributes():
    def released(cond):
        m = module(
            {
                "s": start("g"),
                "g": {"type": "guard", "condition": cond, "transition": {"direct": "go"}},
                "go": {"type": "event", "event": "released", "transition": {"direct": "end"}},
                "end": END,
            },
            attributes={
                "a": {"kind": "constant", "value": 1},
                "b": {"kind": "constant", "value": 0},
            },
        )
        sim = Simulator([m], Population(size=20, start=T0), SimConfig(seed=1))
        return sim.run_until("2021-01-01").num_rows

    a1 = {"type": "attribute", "attribute": "a", "op": "==", "value": 1}
    b1 = {"type": "attribute", "attribute": "b", "op": "==", "value": 1}
    assert released({"type": "and", "conditions": [a1, b1]}) == 0
    assert released({"type": "or", "conditions": [a1, b1]}) == 20
    assert released({"type": "not", "condition": b1}) == 20
    assert released({"type": "at_least", "minimum": 1, "conditions": [a1, b1]}) == 20
    assert released({"type": "at_most", "maximum": 0, "conditions": [a1, b1]}) == 0
