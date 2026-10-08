"""Arrival processes (W2-09 item 3): ``--arrivals poisson`` in ``RateSchedule``."""

from __future__ import annotations

import json
import math
import threading

import numpy as np
import pytest

from shape.cli.main import main
from shape.streaming.emit import (
    Burst,
    EmitConfig,
    EmitRunner,
    MemorySink,
    RateCap,
    RateSchedule,
    contract,
)

N = 100_000
SEED = 7


def gaps(s: RateSchedule, n: int = N) -> np.ndarray:
    times = np.array([s.due_time(i) for i in range(n + 1)])
    return np.diff(times)


def test_poisson_over_100000_events_the_mean_rate_and_the_gap_cv():
    rate = 250.0
    s = RateSchedule(rate, arrivals="poisson", seed=SEED)
    g = gaps(s)
    realised = N / s.due_time(N)
    assert abs(realised - rate) / rate < 0.01  # within 1% of --rate
    cv = g.std() / g.mean()
    assert abs(cv - 1.0) < 0.02  # exponential gaps: coefficient of variation 1
    assert abs(g.mean() - 1 / rate) / (1 / rate) < 0.01
    assert (g >= 0).all()


def test_the_constant_process_is_what_it_was_and_has_no_variation():
    s = RateSchedule(250.0, [Burst(2, 3, 4)])
    assert s.arrivals == "constant"
    g = gaps(s, 5000)
    assert g.std() / g.mean() < 1.0  # a burst changes the rate, not the regularity
    c = RateSchedule(100.0)
    assert [c.due_time(i) for i in (0, 1, 250)] == [0.0, 0.01, 2.5]


def test_the_schedule_is_a_function_of_the_seed_and_the_position():
    a = [RateSchedule(100, arrivals="poisson", seed=SEED).due_time(i) for i in range(0, 5000, 7)]
    b = [RateSchedule(100, arrivals="poisson", seed=SEED).due_time(i) for i in range(0, 5000, 7)]
    c = [
        RateSchedule(100, arrivals="poisson", seed=SEED + 1).due_time(i) for i in range(0, 5000, 7)
    ]
    assert a == b and a != c
    # random access gives the same times as walking from 0 (blocks are cached, not state)
    s = RateSchedule(100, arrivals="poisson", seed=SEED)
    near, far = s.due_time(10), s.due_time(300_000)
    assert RateSchedule(100, arrivals="poisson", seed=SEED).due_time(300_000) == far
    assert s.due_time(10) == near  # going back after a far call (the cache moved) is the same


def test_after_a_resume_the_gaps_are_the_ones_of_the_uninterrupted_schedule():
    full = RateSchedule(100, arrivals="poisson", seed=SEED)
    resumed = RateSchedule(100, arrivals="poisson", seed=SEED)
    resumed.resume_at(70_000)
    for n in (0, 1, 2, 50, 65_535, 65_536, 65_537, 100_000):
        want = full.due_time(70_000 + n) - full.due_time(70_000)
        assert resumed.due_time(n) == pytest.approx(want, abs=1e-6)
    # a constant schedule ignores the position
    c = RateSchedule(100)
    c.resume_at(5)
    assert c.due_time(250) == pytest.approx(2.5)


def test_block_boundaries_are_seamless():
    s = RateSchedule(1.0, arrivals="poisson", seed=SEED)
    edge = 1 << 16
    t = [s.due_time(i) for i in (edge - 1, edge, edge + 1)]
    assert t[0] <= t[1] <= t[2]
    assert (t[1] - t[0]) > 0 and (t[2] - t[1]) > 0


def test_poisson_combines_with_a_burst():
    base, mult = 100.0, 5.0
    s = RateSchedule(base, [Burst(100, 200, mult)], arrivals="poisson", seed=SEED)
    # arrivals per window follow the burst: 100 s at 100/s, 200 s at 500/s, then 100/s
    counts = []
    times = np.array([s.due_time(i) for i in range(0, 130_000)])
    for lo, hi, rate in ((0, 100, base), (100, 300, base * mult), (300, 400, base)):
        n = int(((times >= lo) & (times < hi)).sum())
        expect = rate * (hi - lo)
        counts.append((n, expect))
        assert abs(n - expect) <= 4 * math.sqrt(expect), (lo, hi, n, expect)
    assert s.expected_by(300) == pytest.approx(100 * 100 + 500 * 200)


def test_events_by_is_the_realised_count_and_expected_by_its_mean():
    s = RateSchedule(100, arrivals="poisson", seed=SEED)
    for t in (0.0, 0.5, 10.0, 123.4):
        n = s.events_by(t)
        assert float(n).is_integer() and abs(n - s.expected_by(t)) <= 4 * math.sqrt(100 * t + 1)
        if n:
            assert (
                s.due_time(int(n) - 1) <= t < s.due_time(int(n)) + 1e-12 or s.due_time(int(n)) > t
            )


def test_a_bad_arrival_process_is_refused():
    with pytest.raises(ValueError, match="arrivals must be one of"):
        RateSchedule(10, arrivals="gaussian")


# ---- through the runner ---------------------------------------------------------------------


def test_the_runner_paces_by_the_poisson_schedule_and_the_cap_together():
    deadlines: list[float] = []
    start: list[float] = []

    def sleep_until(_stop: threading.Event, deadline: float) -> None:
        if not start:
            start.append(deadline)
        deadlines.append(deadline - start[0])

    cfg = EmitConfig(
        realtime=True,
        rate=1000.0,
        arrivals="poisson",
        seed=SEED,
        max_rate=400.0,
        max_events=600,
        batch_events=50,
        bursts=(Burst(0.05, 0.05, 3.0),),
    )
    sink = MemorySink()
    runner = EmitRunner(contract.default_plan(), sink, cfg, sleep_until=sleep_until)
    # the schedule is the one the config describes; the cap is the other floor
    assert runner.schedule is not None and runner.schedule.arrivals == "poisson"
    cap = RateCap(400.0)
    for i, batch_start in enumerate(range(0, 600, 50)):
        due = runner._due(sink_batch(50), batch_start)
        want = max(runner.schedule.due_time(batch_start), cap.due(batch_start + 50))
        assert due == pytest.approx(want), i


def sink_batch(n):
    plan = contract.default_plan()
    return next(iter(plan.blocks(0))).batch.slice(0, n)


def test_the_runner_resumes_the_schedule_at_the_checkpoint_offset(tmp_path):
    ck = tmp_path / "ck.json"
    cfg = dict(
        realtime=True,
        rate=1e6,
        arrivals="poisson",
        seed=SEED,
        batch_events=100,
        checkpoint_path=str(ck),
        checkpoint_every=100,
    )
    first = EmitRunner(contract.default_plan(), MemorySink(), EmitConfig(max_events=300, **cfg))
    first.run()
    second = EmitRunner(contract.default_plan(), MemorySink(), EmitConfig(max_events=600, **cfg))
    second.run()
    assert second.schedule is not None and second.schedule._base == 300  # resumed at the offset
    ref = RateSchedule(1e6, arrivals="poisson", seed=SEED)
    assert second.schedule.due_time(100) == pytest.approx(
        ref.due_time(400) - ref.due_time(300), abs=1e-9
    )


# ---- the command line -----------------------------------------------------------------------

BASE = ["emit", "retail", "--table", "customer", "--max-events", "20"]


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--arrivals", "poisson"], "--arrivals poisson needs --realtime"),
        (["--arrivals", "poisson", "--speed", "60x"], "--arrivals poisson needs --realtime"),
    ],
)
def test_poisson_needs_realtime(extra, message, capsys):
    assert main([*BASE, *extra]) == 2
    assert message in capsys.readouterr().err


def test_a_bad_arrivals_choice_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        main([*BASE, "--arrivals", "uniform"])
    assert exc.value.code == 2


def test_constant_is_the_default_and_poisson_runs_end_to_end(capsys):
    assert main([*BASE]) == 0
    plain = capsys.readouterr().out
    assert main([*BASE, "--arrivals", "constant"]) == 0
    assert capsys.readouterr().out == plain
    code = main([*BASE, "--realtime", "--rate", "5000", "--arrivals", "poisson", "--json"])
    assert code == 0
    # W1-14: with --json, what the console sink prints is one shape-result document, the events
    # under payload
    events = json.loads(capsys.readouterr().out)["payload"]
    assert events[:20] == [json.loads(line) for line in plain.splitlines()[:20]]  # unchanged
