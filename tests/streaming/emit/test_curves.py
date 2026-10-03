"""Rate curves (W2-09 item 4): ``--ramp`` and ``--daily-curve`` in ``RateSchedule``."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from shape.cli.main import main
from shape.errors import ShapeError
from shape.streaming.emit import Burst, RateSchedule, VirtualClock
from shape.streaming.emit.rate import (
    BUILTIN_CURVES,
    DailyCurve,
    Ramp,
    load_curve,
    parse_ramp,
)

H = 3600.0
DAY = 86400.0
BUSINESS = BUILTIN_CURVES["business-hours"]


# ---- --ramp ---------------------------------------------------------------------------------


def test_parse_ramp():
    assert parse_ramp("10:20:1:3") == Ramp(10.0, 20.0, 1.0, 3.0)
    assert parse_ramp("0:1.5:0:0.5") == Ramp(0.0, 1.5, 0.0, 0.5)  # from 0 is allowed


@pytest.mark.parametrize(
    "spec",
    ["", "1:2:3", "1:2:3:4:5", "a:1:2:3", "-1:1:2:3", "1:0:2:3", "1:-1:2:3", "1:1:-2:3",
     "1:1:2:-3", "1:1:nan:3", "1:inf:2:3", "1:1:2:inf"],
)  # fmt: skip
def test_parse_ramp_rejects(spec):
    with pytest.raises(ValueError, match="ramp"):
        parse_ramp(spec)


def test_a_ramp_moves_the_rate_linearly_and_holds_its_target():
    s = RateSchedule(100, ramps=[Ramp(10, 20, 1, 3)])
    assert s.expected_by(10) == pytest.approx(1000)  # before the ramp: the base rate
    assert s.expected_by(20) == pytest.approx(1000 + 100 * (10 + 0.05 * 100))  # 1 -> 2 over 10 s
    assert s.expected_by(30) == pytest.approx(1000 + 100 * (20 + 0.1 * 200))  # 4000 more
    assert s.expected_by(40) == pytest.approx(5000 + 10 * 300)  # holds 3x
    for n in (0, 1, 999, 1000, 1500, 5000, 7000):
        assert s.expected_by(s.due_time(n)) == pytest.approx(n, abs=1e-6)


def test_two_ramps_up_then_down_with_a_plateau_between():
    s = RateSchedule(10, ramps=[Ramp(0, 10, 0, 4), Ramp(20, 10, 4, 1)])
    assert s.expected_by(10) == pytest.approx(10 * 10 * 2)  # 0 -> 4 average 2
    assert s.expected_by(20) == pytest.approx(200 + 10 * 40)  # holds 4
    assert s.expected_by(30) == pytest.approx(600 + 10 * 10 * 2.5)  # 4 -> 1 average 2.5
    assert s.expected_by(40) == pytest.approx(850 + 100)  # holds 1


def test_ramps_may_not_overlap_but_may_touch_and_combine_with_bursts():
    with pytest.raises(ValueError, match="ramps may not overlap"):
        RateSchedule(10, ramps=[Ramp(0, 10, 1, 2), Ramp(9, 5, 2, 3)])
    RateSchedule(10, ramps=[Ramp(0, 10, 1, 2), Ramp(10, 5, 2, 3)])  # touching is fine
    s = RateSchedule(10, [Burst(0, 10, 2)], ramps=[Ramp(0, 10, 1, 3)])  # multiply
    assert s.expected_by(10) == pytest.approx(10 * 2 * 20)  # 2 x (1 -> 3 over 10 s) = 2 x 20


def test_a_ramp_to_zero_that_never_recovers_is_refused():
    with pytest.raises(ValueError, match="never delivers"):
        RateSchedule(10, ramps=[Ramp(5, 5, 1, 0)])
    RateSchedule(10, ramps=[Ramp(5, 5, 1, 0), Ramp(20, 5, 0, 1)])  # a pause, then back


# ---- the curve file and the built-ins -------------------------------------------------------


def write_curve(path: Path, points, **extra) -> str:
    doc = {"format": "shape-rate-curve", "version": 1, "points": points, **extra}
    path.write_text(json.dumps(doc))
    return str(path)


def test_the_builtin_curves():
    assert set(BUILTIN_CURVES) == {"flat", "business-hours"}
    flat = BUILTIN_CURVES["flat"]
    assert all(flat.value(t) == 1.0 for t in (0, 12 * H, 86399))
    assert BUSINESS.value(12 * H) == 1.0 and BUSINESS.value(3 * H) == 0.15
    assert BUSINESS.value(8 * H) == pytest.approx(0.575)  # halfway up from 07:00 to 09:00
    assert BUSINESS.value(18 * H) == pytest.approx(0.65)  # halfway down from 17:00 to 19:00
    assert load_curve("flat") is BUILTIN_CURVES["flat"]


def test_a_curve_file_is_interpolated_linearly_and_wraps_at_midnight(tmp_path):
    c = load_curve(
        write_curve(tmp_path / "c.json", [["00:00", 0.2], ["09:00", 1.0], ["18:00", 0.4]])
    )
    assert c.value(0) == pytest.approx(0.2) and c.value(9 * H) == pytest.approx(1.0)
    assert c.value(4.5 * H) == pytest.approx(0.6)
    assert c.value(13.5 * H) == pytest.approx(0.7)
    assert c.value(21 * H) == pytest.approx(0.3)  # 18:00 (0.4) towards 24:00 (0.2): halfway
    assert c.value(DAY - 1e-6) == pytest.approx(0.2, abs=1e-6)
    # the first point is not at midnight: the curve wraps from the last point to the first
    w = load_curve(write_curve(tmp_path / "w.json", [["18:00", 1.0], ["06:00", 0.5]]))
    assert w.value(0) == pytest.approx(0.75)  # 18:00 -> 06:00 next day: midnight is halfway
    assert w.value(6 * H) == 0.5 and w.value(18 * H) == 1.0
    # one point is a constant; point order in the file does not matter
    assert load_curve(write_curve(tmp_path / "o.json", [["12:00", 0.5]])).value(77) == 0.5
    u = load_curve(write_curve(tmp_path / "u.json", [["18:00", 0.4], ["00:00", 0.2]]))
    assert u.value(0) == 0.2 and u.value(18 * H) == 0.4
    assert load_curve(write_curve(tmp_path / "s.json", [["09:30:30", 1.0]])).value(0) == 1.0


@pytest.mark.parametrize(
    ("doc", "message"),
    [
        ({"format": "other", "version": 1, "points": [["00:00", 1]]}, "not a shape-rate-curve"),
        ({"format": "shape-rate-curve", "points": [["00:00", 1]]}, "integer"),
        ({"format": "shape-rate-curve", "version": "1", "points": [["00:00", 1]]}, "integer"),
        ({"format": "shape-rate-curve", "version": True, "points": [["00:00", 1]]}, "integer"),
        (
            {"format": "shape-rate-curve", "version": 2, "points": [["00:00", 1]]},
            "version 2, which is newer than the version 1",
        ),
        ({"format": "shape-rate-curve", "version": 1}, "points"),
        ({"format": "shape-rate-curve", "version": 1, "points": []}, "at least one point"),
        ({"format": "shape-rate-curve", "version": 1, "points": "x"}, "points"),
        ({"format": "shape-rate-curve", "version": 1, "points": [["00:00"]]}, "[time, multiplier]"),
        ({"format": "shape-rate-curve", "version": 1, "points": [["25:00", 1]]}, "HH:MM"),
        ({"format": "shape-rate-curve", "version": 1, "points": [["24:00", 1]]}, "HH:MM"),
        ({"format": "shape-rate-curve", "version": 1, "points": [["9:00", 1]]}, "HH:MM"),
        ({"format": "shape-rate-curve", "version": 1, "points": [["09:60", 1]]}, "HH:MM"),
        ({"format": "shape-rate-curve", "version": 1, "points": [[900, 1]]}, "HH:MM"),
        ({"format": "shape-rate-curve", "version": 1, "points": [["09:00", -1]]}, "multiplier"),
        ({"format": "shape-rate-curve", "version": 1, "points": [["09:00", True]]}, "multiplier"),
        ({"format": "shape-rate-curve", "version": 1, "points": [["09:00", "1"]]}, "multiplier"),
        (
            {"format": "shape-rate-curve", "version": 1, "points": [["09:00", 1], ["09:00", 2]]},
            "twice",
        ),
        ({"format": "shape-rate-curve", "version": 1, "points": [["09:00", 0]]}, "all 0"),
        (
            {"format": "shape-rate-curve", "version": 1, "points": [["09:00", 1]], "x": 1},
            "unknown key 'x'",
        ),
        ([1, 2], "JSON object"),
    ],
)
def test_a_bad_curve_file_is_refused(tmp_path, doc, message):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(doc))
    with pytest.raises(ShapeError, match=message.replace("[", r"\[").replace("]", r"\]")):
        load_curve(str(path))


def test_unknown_names_and_unreadable_files():
    with pytest.raises(ShapeError, match="built-in curves are flat, business-hours"):
        load_curve("lunch-rush")
    with pytest.raises(ShapeError, match="not valid JSON"):
        p = Path("not-json.tmp")
        p.write_text("{")
        try:
            load_curve(str(p))
        finally:
            p.unlink()


# ---- the integral of the rate ---------------------------------------------------------------


def trapezoid_rate_integral(rate_at, t0: float, t1: float, step: float = 0.05) -> float:
    ts = np.arange(t0, t1 + step, step)
    return float(np.trapezoid(rate_at(ts), ts))


def independent_multiplier(curve_points, origin: float):
    """The curve's multiplier at wall-clock seconds, from the points alone (numpy interpolation,
    a wrap added by hand), as an independent check of ``DailyCurve``."""
    xs = [p[0] for p in curve_points]
    ys = [p[1] for p in curve_points]
    xs_ext = [xs[-1] - DAY, *xs, xs[0] + DAY]
    ys_ext = [ys[-1], *ys, ys[0]]

    def at(t):
        return np.interp((origin + t) % DAY, xs_ext, ys_ext)

    return at


POINTS = [(0.0, 0.2), (9 * H, 1.0), (18 * H, 0.4)]
CURVE = DailyCurve(POINTS)


@pytest.mark.parametrize("origin", [0.0, 8.7 * H, 23.9 * H])
def test_the_delivered_count_over_a_window_is_the_integral_of_the_rate(origin):
    rate = 1.5
    s = RateSchedule(rate, curve=CURVE, day_origin=origin)
    m = independent_multiplier(POINTS, origin)
    windows = [(0, 3600), (3 * H, 4 * H), (8 * H, 11 * H), (20 * H, 26 * H), (30 * H, 49 * H)]
    for t0, t1 in windows:
        n0, n1 = math.ceil(s.expected_by(t0)), math.ceil(s.expected_by(t1))
        # events delivered in [t0, t1): those whose due time is in the window
        first = _first_due_at_or_after(s, t0, n0)
        last = _first_due_at_or_after(s, t1, n1)
        delivered = last - first
        want = trapezoid_rate_integral(lambda ts: rate * m(ts), t0, t1)
        assert abs(delivered - want) <= 1.0 + 1e-6 * want, (origin, t0, t1, delivered, want)


def _first_due_at_or_after(s: RateSchedule, t: float, guess: int) -> int:
    """The index of the first event due at or after ``t`` (events 0, 1, 2, ... in order)."""
    n = max(0, guess - 2)
    while s.due_time(n) < t:
        n += 1
    return n


def test_the_whole_day_and_the_wrap():
    s = RateSchedule(2.0, curve=CURVE)
    day = s.expected_by(DAY)
    assert day == pytest.approx(
        2.0
        * trapezoid_rate_integral(
            lambda ts: np.interp(ts, [0, 9 * H, 18 * H, DAY], [0.2, 1.0, 0.4, 0.2]), 0, DAY, 0.5
        )
    )
    assert s.expected_by(3 * DAY) == pytest.approx(3 * day)
    assert s.expected_by(DAY + 9 * H) == pytest.approx(day + s.expected_by(9 * H))


def test_ramps_bursts_and_a_curve_multiply_and_invert_exactly():
    rate = 3.0
    ramps = [Ramp(100, 5000, 0.5, 2.0), Ramp(20_000, 3000, 2.0, 0.25)]
    bursts = [Burst(2000, 600, 3.0), Burst(21_000, 100, 4.0)]
    origin = 7.0 * H
    s = RateSchedule(rate, bursts, ramps=ramps, curve=CURVE, day_origin=origin)
    m = independent_multiplier(POINTS, origin)

    def rate_at(ts):
        out = rate * m(ts)
        for r in ramps:
            level = np.where(
                ts < r.start,
                1.0,
                np.where(
                    ts >= r.start + r.duration,
                    r.to_mult,
                    r.from_mult + (r.to_mult - r.from_mult) * (ts - r.start) / r.duration,
                ),
            )
            # ramps after the first one only apply from their start (before it, the earlier level)
            if r is ramps[0]:
                first_level = level
            else:
                first_level = np.where(ts < r.start, first_level, level)
        out = out * first_level
        for b in bursts:
            out = out * np.where((ts >= b.start) & (ts < b.end), b.mult, 1.0)
        return out

    for t in (1.0, 99.0, 100.0, 2000.0, 2300.0, 2600.0, 5100.0, 20_500.0, 21_050.0, 30_000.0):
        want = trapezoid_rate_integral(rate_at, 0.0, t, 0.02)
        assert s.expected_by(t) == pytest.approx(want, rel=1e-4, abs=0.01), t
    for n in (0, 1, 7, 500, 4_000, 20_000, 60_000):
        assert s.expected_by(s.due_time(n)) == pytest.approx(n, abs=1e-5), n
    times = [s.due_time(n) for n in range(0, 3000)]
    assert times == sorted(times)


def test_a_zero_multiplier_pauses_the_stream():
    c = DailyCurve([(0.0, 0.0), (6 * H, 0.0), (7 * H, 1.0), (22 * H, 1.0), (23 * H, 0.0)])
    s = RateSchedule(10.0, curve=c)
    assert s.expected_by(6 * H) == 0.0
    assert s.due_time(1) > 6 * H  # nothing is due in the quiet hours
    assert s.due_time(0) == 0.0
    assert s.expected_by(s.due_time(100)) == pytest.approx(100)
    # an event due exactly when the pause begins is due then, not after the pause
    full = s.expected_by(22 * H + 0.0)
    n = int(full)
    assert s.due_time(n) <= 23 * H


def test_the_curve_follows_the_wall_clock_origin():
    a = RateSchedule(10.0, curve=BUSINESS, day_origin=3 * H)  # starts in the quiet night
    b = RateSchedule(10.0, curve=BUSINESS, day_origin=10 * H)  # starts at the busy time
    assert a.expected_by(600) == pytest.approx(10 * 0.15 * 600)
    assert b.expected_by(600) == pytest.approx(10 * 600)
    assert RateSchedule(10.0, curve=BUILTIN_CURVES["flat"]).expected_by(600) == pytest.approx(6000)


def test_a_poisson_stream_follows_the_curve():
    s = RateSchedule(2.0, curve=CURVE, arrivals="poisson", seed=3, day_origin=0.0)
    times = np.array([s.due_time(i) for i in range(0, 60_000)])
    assert times.max() > 10 * H  # the windows below are inside the events drawn
    for lo, hi in ((0, 4 * H), (6 * H, 8 * H), (8 * H, 10 * H)):
        n = int(((times >= lo) & (times < hi)).sum())
        expect = s.expected_by(hi) - s.expected_by(lo)
        assert abs(n - expect) <= 4 * math.sqrt(expect) + 1, (lo, hi, n, expect)


def test_a_curve_is_only_for_a_positive_rate_overall():
    with pytest.raises(ShapeError, match="all 0"):
        DailyCurve([(0.0, 0.0), (3 * H, 0.0)])


# ---- --speed: the curve follows the event time ----------------------------------------------


def test_a_curve_on_a_virtual_clock_scales_the_replay_speed_by_event_time():
    t0 = 1_700_000_000.0  # a UTC instant
    plain = VirtualClock(60.0)
    flat = VirtualClock(60.0, curve=BUILTIN_CURVES["flat"])
    for t in (t0, t0 + 10, t0 + 3600, t0 + 40_000):
        assert flat.due(t) == pytest.approx(plain.due(t))
    # the wall time of an event is the integral of 1 / (speed x multiplier) over the event time
    xs = np.arange(t0, t0 + 86_400, 1.0)
    tod = xs % DAY
    mult = np.interp(
        tod,
        [0, 7 * H, 9 * H, 17 * H, 19 * H, 22 * H, DAY],
        [0.15, 0.15, 1.0, 1.0, 0.3, 0.15, 0.15],
    )
    walk = np.concatenate([[0.0], np.cumsum(1.0 / (60.0 * mult[:-1]))])
    clock = VirtualClock(60.0, curve=BUSINESS)
    for k in (0, 600, 5000, 30_000, 80_000):
        assert clock.due(float(xs[k])) == pytest.approx(walk[k], rel=1e-3, abs=1e-3), k
    # the same 10 minutes of event time take longer to replay at night (multiplier 0.15) than in
    # the busy middle of the day (1.0): the busy hours deliver more events per second of wall time
    midnight = 1_700_000_000.0 - 1_700_000_000.0 % DAY  # a UTC midnight
    wall = {}
    for label, hour in (("night", 3), ("day", 12)):
        c = VirtualClock(60.0, curve=BUSINESS)
        start = midnight + hour * H
        c.due(start)
        wall[label] = c.due(start + 600)
    assert wall["night"] == pytest.approx(600 / (60 * 0.15)) and wall["day"] == pytest.approx(10.0)


def test_an_event_that_is_earlier_than_one_already_sent_goes_at_once_with_a_curve():
    c = VirtualClock(10.0, curve=BUSINESS)
    first = c.due(1_000_000.0)
    later = c.due(1_000_500.0)
    assert later > first
    assert c.due(1_000_100.0) == later  # earlier than one sent: never backwards
    assert c.due(None) == later


def test_a_curve_with_a_zero_cannot_pace_a_replay():
    with pytest.raises(ShapeError, match="0 multiplier"):
        VirtualClock(10.0, curve=DailyCurve([(0.0, 0.0), (9 * H, 1.0)]))


# ---- the command line -----------------------------------------------------------------------

BASE = ["emit", "retail", "--table", "customer", "--max-events", "20"]


def test_ramp_and_curve_flags_need_realtime(capsys):
    assert main([*BASE, "--ramp", "0:1:1:2"]) == 2
    assert "--ramp needs --realtime" in capsys.readouterr().err
    assert main([*BASE, "--daily-curve", "flat"]) == 2
    assert "--daily-curve needs --realtime or --speed" in capsys.readouterr().err


def test_overlapping_ramps_exit_2_with_the_documented_message(capsys):
    argv = [*BASE, "--realtime", "--ramp", "0:10:1:2", "--ramp", "5:10:2:3"]
    assert main(argv) == 2
    assert "ramps may not overlap" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--ramp", "0:1:1"], "ramp"),
        (["--ramp", "0:-1:1:2"], "DURATION must be positive"),
        (["--daily-curve", "no-such-curve"], "built-in curves are flat, business-hours"),
    ],
)
def test_bad_ramps_and_curves_exit_2(extra, message, capsys):
    assert main([*BASE, "--realtime", *extra]) == 2
    assert message in capsys.readouterr().err


def test_flat_curve_and_no_curve_deliver_the_same_events(capsys, tmp_path):
    assert main([*BASE]) == 0
    plain = capsys.readouterr().out
    curve = write_curve(tmp_path / "c.json", [["00:00", 0.5], ["12:00", 1.0]])
    for extra in (
        ["--realtime", "--rate", "20000", "--daily-curve", "business-hours"],
        ["--realtime", "--rate", "20000", "--daily-curve", curve, "--ramp", "0:0.05:1:2"],
        ["--speed", "100000x", "--daily-curve", "business-hours"],
    ):
        assert main([*BASE, *extra]) == 0, extra
        assert capsys.readouterr().out == plain, extra


def test_a_version_1_curve_file_still_reads(tmp_path):
    """The persisted format, frozen: this exact text is a version 1 ``shape-rate-curve`` file and
    must keep reading as these multipliers (a newer version is refused: see above)."""
    path = tmp_path / "v1.json"
    path.write_text(
        '{"format": "shape-rate-curve", "version": 1, '
        '"points": [["00:00", 0.2], ["09:00", 1.0], ["18:00", 0.4]]}'
    )
    c = load_curve(str(path))
    assert [c.value(t) for t in (0, 9 * H, 18 * H)] == [0.2, 1.0, 0.4]


def test_the_runner_starts_the_curve_at_the_wall_clock_time_of_day(monkeypatch):
    from shape.streaming.emit import EmitConfig, EmitRunner, MemorySink, contract

    monkeypatch.setattr("shape.streaming.emit.runtime._seconds_of_day", lambda: 10 * H)
    cfg = dict(realtime=True, rate=1e6, curve=BUSINESS, max_events=300, batch_events=100)
    runner = EmitRunner(contract.default_plan(), MemorySink(), EmitConfig(**cfg))
    assert runner.run().events == 300
    assert runner.schedule is not None and runner.schedule.day_origin == 10 * H
    # an explicit origin is kept
    runner = EmitRunner(
        contract.default_plan(), MemorySink(), EmitConfig(curve_origin=3 * H, **cfg)
    )
    runner.run()
    assert runner.schedule is not None and runner.schedule.day_origin == 3 * H
    assert runner.schedule.expected_by(10) == pytest.approx(1e6 * 0.15 * 10)


def test_the_config_refuses_curves_and_ramps_that_cannot_apply():
    from shape.streaming.emit import EmitConfig, EmitRunner, MemorySink, contract

    plan = contract.default_plan()
    with pytest.raises(ValueError, match="ramps need realtime"):
        EmitRunner(plan, MemorySink(), EmitConfig(ramps=(Ramp(0, 1, 1, 2),)))
    with pytest.raises(ValueError, match="curve needs realtime pacing or a speed"):
        EmitRunner(plan, MemorySink(), EmitConfig(curve=BUSINESS))
    EmitRunner(plan, MemorySink(), EmitConfig(speed=60.0, curve=BUSINESS))  # event time: fine
