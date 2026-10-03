"""Delays run on the virtual clock, never the wall clock."""

import numpy as np
import pyarrow.compute as pc
from helpers import END, T0, counts, module, run, start
from shape_behavior import Population, SimConfig, Simulator

DAY = 86_400 * 1_000_000


def _us(ts) -> np.ndarray:
    return (
        np.asarray(ts.cast("timestamp[us]").to_numpy(zero_copy_only=False))
        .astype("datetime64[us]")
        .astype(np.int64)
    )


def _kind(t, kind):
    return t.filter(pc.equal(t.column("kind"), kind)).column("time")


def _chain(*delays):
    states = {"s": start("e0")}
    for i, d in enumerate(delays):
        states[f"e{i}"] = {"type": "event", "event": f"step{i}", "transition": {"direct": f"d{i}"}}
        states[f"d{i}"] = {"type": "delay", "delay": d, "transition": {"direct": f"e{i + 1}"}}
    states[f"e{len(delays)}"] = {
        "type": "event",
        "event": f"step{len(delays)}",
        "transition": {"direct": "end"},
    }
    states["end"] = END
    return module(states)


def test_exact_delays_add_up_to_the_event_times():
    m = _chain(
        {"kind": "exact", "value": 10, "unit": "days"},
        {"kind": "exact", "value": 2, "unit": "weeks"},
    )
    t = run([m], size=50, until="2022-01-01")
    start_us = int(np.datetime64(T0, "us").astype(np.int64))
    by = {k: set(_us(_kind(t, k))) for k in ("step0", "step1", "step2")}
    assert by["step0"] == {start_us}
    assert by["step1"] == {start_us + 10 * DAY}
    assert by["step2"] == {start_us + 24 * DAY}


def test_units():
    for unit, days in (("hours", 1 / 24), ("weeks", 7), ("months", 30.4375), ("years", 365.25)):
        m = _chain({"kind": "exact", "value": 1, "unit": unit})
        t = run([m], size=3, until="2040-01-01")
        times = _us(_kind(t, "step1"))
        assert set(times - _us(_kind(t, "step0"))) == {round(days * DAY)}


def test_exponential_delay_has_the_requested_mean():
    m = _chain({"kind": "exponential", "mean": 40, "unit": "days"})
    n = 30_000
    t = run([m], size=n, until="2200-01-01", seed=3)
    a = _us(_kind(t, "step0"))
    b = _us(_kind(t, "step1"))
    gaps = (b - a) / DAY
    assert abs(gaps.mean() - 40) < 4.5 * 40 / np.sqrt(n)


def test_gaussian_delay_clips_at_zero_and_uniform_stays_in_range():
    g = _chain({"kind": "gaussian", "mean": 5, "std": 20, "unit": "days"})
    t = run([g], size=5000, until="2100-01-01", seed=1)
    a = _us(_kind(t, "step0"))
    b = _us(_kind(t, "step1"))
    assert (b - a).min() >= 0
    u = _chain({"kind": "uniform", "low": 3, "high": 9, "unit": "days"})
    t = run([u], size=5000, until="2100-01-01", seed=1)
    a = _us(_kind(t, "step0"))
    b = _us(_kind(t, "step1"))
    gaps = (b - a) / DAY
    assert 3 <= gaps.min() and gaps.max() <= 9 and abs(gaps.mean() - 6) < 0.1


def test_events_past_the_horizon_wait_for_a_later_run_until():
    m = _chain({"kind": "exact", "value": 400, "unit": "days"})
    sim = Simulator([m], Population(size=20, start=T0), SimConfig(seed=1))
    first = sim.run_until("2020-12-31")  # day 365: the step after the 400-day delay is not due yet
    assert counts(first, "kind") == {"step0": 20}
    second = sim.run_until("2021-12-31")
    assert counts(second, "kind") == {"step1": 20}
    assert str(sim.now).startswith("2021-12-31")


def test_the_run_does_not_read_the_wall_clock(monkeypatch):
    import time

    def boom(*_a, **_k):
        raise AssertionError("the simulator read the wall clock")

    monkeypatch.setattr(time, "time", boom)
    monkeypatch.setattr(time, "monotonic", boom)
    m = _chain({"kind": "exact", "value": 3, "unit": "days"})
    assert run([m], size=10).num_rows == 20


def test_arrival_spreads_entities_over_the_window():
    m = _chain({"kind": "exact", "value": 1, "unit": "days"})
    t = run([m], size=4000, until="2030-01-01", seed=2, arrival={"kind": "uniform", "days": 365})
    first = _us(_kind(t, "step0"))
    start_us = int(np.datetime64(T0, "us").astype(np.int64))
    assert first.min() >= start_us and first.max() <= start_us + 365 * DAY
    assert abs((first.mean() - start_us) / DAY - 182.5) < 8


def test_lifetime_ends_the_entity():
    m = _chain({"kind": "exact", "value": 100, "unit": "days"})
    t = run(
        [m],
        size=200,
        until="2030-01-01",
        seed=2,
        lifetime={"kind": "exact", "value": 50, "unit": "days"},
    )
    c = counts(t, "kind")
    assert c == {"step0": 200, "entity_end": 200}  # the 100-day step never happens
