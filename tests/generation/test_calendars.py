"""P4-05: calendars (rules, events, paydays, period ends, trends), timestamp generation from a
profile, and profiler detection of month, day-of-week and hour profiles, holiday lifts and the
tail index."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np
import pyarrow as pa
import pytest

from shape.builtins.calendars import (
    CompositeCalendar,
    EasterOffset,
    Event,
    FixedDate,
    NthWeekday,
    Observed,
    Offset,
    Payday,
    PeriodEnd,
    Trend,
    UsFederalCalendar,
    UsRetailCalendar,
    calendar_from_spec,
    easter,
    rule_from_spec,
)
from shape.builtins.distributions.families import family_by_name
from shape.generation.rng import RowStream
from shape.generation.temporal import day_probabilities, sample_timestamps
from shape.plugins import kit
from shape.profile.temporal import holiday_lifts, tail_index, temporal_profile


def _days(a: date, b: date) -> list[date]:
    return [a + timedelta(days=i) for i in range((b - a).days + 1)]


# ---------------------------------------------------------------- rules (D-11)


def test_rules_give_the_known_dates():
    thanksgiving = NthWeekday(11, 3, 4)
    assert [thanksgiving.on(y) for y in (2023, 2024, 2025)] == [
        date(2023, 11, 23), date(2024, 11, 28), date(2025, 11, 27)
    ]  # fmt: skip
    assert Offset(thanksgiving, 1).on(2024) == date(2024, 11, 29)  # Black Friday
    assert NthWeekday(5, 0, -1).on(2024) == date(2024, 5, 27)  # Memorial Day
    assert [EasterOffset().on(y) for y in (2023, 2024, 2025)] == [
        date(2023, 4, 9), date(2024, 3, 31), date(2025, 4, 20)
    ]  # fmt: skip
    assert EasterOffset(-2).on(2024) == date(2024, 3, 29)  # Good Friday
    assert easter(1961) == date(1961, 4, 2) and easter(2038) == date(2038, 4, 25)
    assert Observed(FixedDate(7, 4)).on(2026) == date(2026, 7, 3)  # a Saturday: the Friday
    assert Observed(FixedDate(7, 4)).on(2027) == date(2027, 7, 5)  # a Sunday: the Monday
    assert Observed(FixedDate(7, 4)).on(2025) == date(2025, 7, 4)
    assert FixedDate(2, 29).on(2024) == date(2024, 2, 29) and FixedDate(2, 29).on(2023) is None
    assert FixedDate(6, 19, from_year=2021).on(2020) is None


def test_rules_from_specs():
    assert rule_from_spec({"month": 3, "day": 15}).on(2024) == date(2024, 3, 15)
    assert rule_from_spec({"month": 11, "weekday": "Thursday", "n": 4}).on(2024) == date(
        2024, 11, 28
    )
    assert rule_from_spec({"month": 5, "weekday": 0, "n": -1}).on(2024) == date(2024, 5, 27)
    assert rule_from_spec({"easter": 1}).on(2024) == date(2024, 4, 1)
    assert rule_from_spec({"after": {"month": 11, "weekday": "thu", "n": 4}, "days": 4}).on(
        2024
    ) == date(2024, 12, 2)  # Cyber Monday
    assert rule_from_spec({"observed": {"month": 12, "day": 25}}).on(2021) == date(2021, 12, 24)
    for bad in (
        {"month": 13, "day": 1},
        {"month": 1, "weekday": "xyz"},
        {"month": 1, "weekday": 9},
    ):
        with pytest.raises(ValueError):
            rule_from_spec(bad)


# ---------------------------------------------------------------- events


def test_event_lift_ramp_up_and_decay():
    ev = Event("sale", 3.0, None, (date(2024, 6, 10),), ramp_up_days=2, decay_days=3)
    f = ev.factors(date(2024, 6, 5), date(2024, 6, 16))
    got = dict(zip(_days(date(2024, 6, 5), date(2024, 6, 16)), f.tolist(), strict=True))
    assert got[date(2024, 6, 10)] == 3.0
    assert got[date(2024, 6, 9)] == pytest.approx(
        1 + 2 * (1 - 1 / 3)
    )  # one day out of a 2-day ramp
    assert got[date(2024, 6, 8)] == pytest.approx(1 + 2 * (1 - 2 / 3))
    assert got[date(2024, 6, 7)] == 1.0 and got[date(2024, 6, 14)] == 1.0
    assert [got[date(2024, 6, d)] for d in (11, 12, 13)] == pytest.approx(
        [1 + 2 * (1 - k / 4) for k in (1, 2, 3)]
    )
    exp = Event("e", 3.0, None, (date(2024, 6, 10),), decay_days=3, decay="exponential")
    g = exp.factors(date(2024, 6, 10), date(2024, 6, 14))
    assert g[1] == pytest.approx(1 + 2 * np.exp(-3 / 4)) and g[3] > 1.0 and g[4] == 1.0


def test_negative_lifts_are_dips_and_zero_closes_a_day():
    dip = Event("quiet", 0.25, FixedDate(12, 25), decay_days=1)
    f = dip.factors(date(2024, 12, 24), date(2024, 12, 27))
    assert f.tolist() == pytest.approx([1.0, 0.25, 1 + (0.25 - 1) * 0.5, 1.0])
    assert Event("closed", 0.0, FixedDate(1, 1)).factors(
        date(2024, 1, 1), date(2024, 1, 2)
    ).tolist() == [0.0, 1.0]
    for bad in (-0.1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            Event("x", bad, FixedDate(1, 1))


def test_overlapping_events_multiply_and_ranges_are_slices():
    a = Event("a", 2.0, None, (date(2024, 3, 10),), ramp_up_days=2, decay_days=2)
    b = Event("b", 1.5, None, (date(2024, 3, 11),), decay_days=1)
    comp = CompositeCalendar([a, b])
    whole = comp.factors(date(2024, 3, 1), date(2024, 3, 31))
    day = date(2024, 3, 11)
    assert whole[(day - date(2024, 3, 1)).days] == pytest.approx(
        (1 + 1 * (1 - 1 / 3)) * 1.5 * 1.0 * 1.0
    )
    for lo, hi in [(date(2024, 3, 11), date(2024, 3, 31)), (date(2024, 3, 9), date(2024, 3, 13))]:
        part = comp.factors(lo, hi)
        assert part.tolist() == whole[(lo - date(2024, 3, 1)).days :][: len(part)].tolist()


def test_events_validate_their_arguments():
    with pytest.raises(ValueError, match="rule or dates"):
        Event("x", 2.0)
    with pytest.raises(ValueError):
        Event("x", 2.0, FixedDate(1, 1), ramp_up_days=-1)
    with pytest.raises(ValueError):
        Event("x", 2.0, FixedDate(1, 1), decay="step")


# ---------------------------------------------------------------- paydays and period ends


def test_payday_dates_and_lift():
    pay = Payday(1.3)
    f = pay.factors(date(2024, 6, 1), date(2024, 8, 31))
    hot = {date(2024, 6, 1) + timedelta(days=i) for i, v in enumerate(f) if v > 1}
    # Jun 1 is a Saturday (so Fri May 31), Jun 15 a Saturday (Fri Jun 14), Jul 1 Mon, Jul 15 Mon,
    # Aug 1 Thu, Aug 15 Thu, Sep 1 a Sunday (Fri Aug 30)
    assert hot == {
        date(2024, 6, 14),
        date(2024, 7, 1),
        date(2024, 7, 15),
        date(2024, 8, 1),
        date(2024, 8, 15),
        date(2024, 8, 30),
    }
    assert Payday(1.3, adjust="none").factors(date(2024, 6, 1), date(2024, 6, 1))[0] == 1.3
    bi = Payday(1.5, kind="biweekly", anchor=date(2024, 1, 5))
    hot = [
        date(2024, 1, 1) + timedelta(days=i)
        for i, v in enumerate(bi.factors(date(2024, 1, 1), date(2024, 3, 1)))
        if v > 1
    ]
    assert hot == [date(2024, 1, 5) + timedelta(days=14 * k) for k in range(5)]
    last = Payday(1.2, kind="monthly", days=(-1,), adjust="none").factors(
        date(2024, 2, 1), date(2024, 3, 31)
    )
    assert (
        [v > 1 for v in last].count(True) == 2
        and last[28] > 1
        and last[(date(2024, 3, 31) - date(2024, 2, 1)).days] > 1
    )
    wk = Payday(1.1, decay_days=2).factors(date(2024, 7, 1), date(2024, 7, 5))
    assert wk[0] == 1.1 and 1.0 < wk[1] < 1.1
    for bad in ({"kind": "weekly"}, {"adjust": "next"}):
        with pytest.raises(ValueError):
            Payday(1.2, **bad)


def test_month_end_and_quarter_end():
    f = PeriodEnd(1.2, "month", 3).factors(date(2024, 1, 25), date(2024, 3, 5))
    days = _days(date(2024, 1, 25), date(2024, 3, 5))
    hot = [d for d, v in zip(days, f, strict=True) if v > 1]
    assert hot == [date(2024, 1, 29), date(2024, 1, 30), date(2024, 1, 31),
                   date(2024, 2, 27), date(2024, 2, 28), date(2024, 2, 29)]  # fmt: skip
    q = PeriodEnd(1.5, "quarter", 2).factors(date(2024, 3, 1), date(2024, 7, 5))
    qdays = _days(date(2024, 3, 1), date(2024, 7, 5))
    assert [d for d, v in zip(qdays, q, strict=True) if v > 1] == [
        date(2024, 3, 30), date(2024, 3, 31), date(2024, 6, 29), date(2024, 6, 30)
    ]  # fmt: skip
    with pytest.raises(ValueError):
        PeriodEnd(1.2, "year")


def test_trend_growth_steps_and_ramps():
    t = Trend(annual_growth=0.1, origin=date(2024, 1, 1))
    f = t.factors(date(2024, 1, 1), date(2025, 1, 1))
    assert f[0] == 1.0 and f[-1] == pytest.approx(1.1 ** (366 / 365.25))
    step = Trend(steps=((date(2024, 5, 1), 1.5),)).factors(date(2024, 4, 29), date(2024, 5, 2))
    assert step.tolist() == [1.0, 1.0, 1.5, 1.5]
    ramp = Trend(ramps=((date(2024, 1, 1), date(2024, 1, 11), 2.0),))
    r = ramp.factors(date(2023, 12, 31), date(2024, 1, 14))
    assert r[0] == 1.0 and r[1] == 1.0 and r[6] == pytest.approx(1.5) and r[-1] == 2.0
    with pytest.raises(ValueError):
        Trend(annual_growth=-1.0)
    with pytest.raises(ValueError):
        Trend(ramps=((date(2024, 2, 1), date(2024, 1, 1), 2.0),))


# ---------------------------------------------------------------- the calendars of the plan


def test_us_calendars_with_lifts_ramps_and_decay():
    cal = UsRetailCalendar(
        holiday_lift=1.0,
        lifts={"black_friday": 3.0, "christmas_day": 0.3},
        ramp_up_days=1,
        decay_days=1,
    )
    f = cal.factors(date(2024, 11, 27), date(2024, 12, 3))
    by = dict(zip(_days(date(2024, 11, 27), date(2024, 12, 3)), f.tolist(), strict=True))
    assert by[date(2024, 11, 29)] == pytest.approx(3.0)
    assert by[date(2024, 11, 28)] == pytest.approx(
        1 + 2 * 0.5
    )  # Thanksgiving, in Black Friday's ramp
    assert by[date(2024, 11, 30)] == pytest.approx(1 + 2 * 0.5)
    assert cal.holidays(date(2024, 11, 1), date(2024, 12, 31))[date(2024, 12, 2)] == "cyber_monday"
    with pytest.raises(ValueError, match="unknown holidays"):
        UsRetailCalendar(lifts={"boxing_day": 2.0})
    fed = UsFederalCalendar(holiday_lift=0.5).factors(date(2024, 12, 24), date(2024, 12, 26))
    assert fed.tolist() == [1.0, 0.5, 1.0]


def test_spec_builds_a_composite_and_conforms_to_the_plugin_kit():
    spec = {
        "calendars": [{"name": "us_retail", "lifts": {"black_friday": 2.0}}],
        "events": [{"name": "launch", "date": "2024-05-01", "lift": 4.0, "decay_days": 2}],
        "payday": {"kind": "biweekly", "anchor": "2024-01-05", "lift": 1.2},
        "month_end": {"lift": 1.1, "days": 2},
        "quarter_end": {"lift": 1.3, "days": 1},
        "trend": {"annual_growth": 0.05, "steps": [{"date": "2024-07-01", "factor": 0.8}]},
    }
    cal = calendar_from_spec(spec)
    kit.check_calendar(cal)
    f = cal.factors(date(2024, 4, 28), date(2024, 5, 4))
    assert f[3] > 3.9 and f[4] > 1.5 and f[5] > 1.0  # the launch and its decay
    assert (
        CompositeCalendar().with_spec({}).factors(date(2024, 1, 1), date(2024, 1, 3)).tolist()
        == [1.0] * 3
    )
    with pytest.raises(ValueError, match="unknown calendar"):
        calendar_from_spec({"calendars": ["no_such_country"]})


# ---------------------------------------------------------------- generation and detection


MONTH = [1.0, 1.0, 1.2, 1.2, 1.4, 1.4, 1.0, 1.0, 1.2, 1.4, 2.4, 3.0]
DOW = [1.0, 0.9, 0.9, 1.0, 1.4, 2.0, 1.6]


def _generate(n: int, seed: int, start: date, end: date, **kw: Any) -> pa.Array:
    return sample_timestamps(RowStream(seed, "orders", "placed_at", "v"), 0, n, start, end, **kw)


def _tvd(a: Any, b: Any) -> float:
    a, b = np.asarray(a, float), np.asarray(b, float)
    return 0.5 * float(np.abs(a / a.sum() - b / b.sum()).sum())


def test_profile_generate_profile_month_dow_hour_tvd():
    start, end = date(2021, 1, 1), date(2024, 12, 31)
    source = _generate(
        1_000_000, 11, start, end, month=MONTH, day_of_week=DOW, hour_peaks=([12, 19], 2.5)
    )
    prof = temporal_profile(source)
    assert prof.hour is not None and prof.n == 1_000_000
    assert (prof.start, prof.end) == (start, end)
    assert abs(sum(prof.month) - 1) < 1e-12
    # what was configured comes back
    assert _tvd(prof.month, MONTH) <= 0.01 and _tvd(prof.day_of_week, DOW) <= 0.01
    from shape.generation import kernel_ops

    assert (
        _tvd(prof.hour, kernel_ops.hour_weights_peaks([12, 19], 2.5).to_numpy(zero_copy_only=False))
        <= 0.01
    )
    # profile -> generate -> profile
    again = temporal_profile(
        _generate(
            1_000_000,
            12,
            prof.start,
            prof.end,
            month=prof.month,
            day_of_week=prof.day_of_week,
            hour=prof.hour,
        )
    )
    assert _tvd(again.month, prof.month) <= 0.01
    assert _tvd(again.day_of_week, prof.day_of_week) <= 0.01
    assert _tvd(again.hour, prof.hour) <= 0.01


def test_calendar_lifts_are_recovered_within_five_percent():
    """Black Friday, Cyber Monday and Christmas: configured, generated, detected again."""
    start, end = date(2019, 1, 1), date(2024, 12, 31)
    lifts = {"black_friday": 3.0, "cyber_monday": 2.5, "christmas_day": 0.4}
    spec = {
        "events": [
            {"name": "black_friday", "lift": 3.0, "ramp_up_days": 2,
             "rule": {"after": {"month": 11, "weekday": "thu", "n": 4}, "days": 1}},
            {"name": "cyber_monday", "lift": 2.5, "decay_days": 2,
             "rule": {"after": {"month": 11, "weekday": "thu", "n": 4}, "days": 4}},
            {"name": "christmas_day", "lift": 0.4, "decay_days": 2,
             "rule": {"month": 12, "day": 25}},
        ]
    }  # fmt: skip
    ts = _generate(6_000_000, 21, start, end, month=MONTH, day_of_week=DOW, calendar=spec)
    holidays = {
        d: name
        for name in lifts
        for d in Event(name, 1.0, rule_from_spec(
            next(e["rule"] for e in spec["events"] if e["name"] == name)
        )).occurrences(start, end)
    }  # fmt: skip
    found = holiday_lifts(ts, holidays, exclude_days=4)
    for name, want in lifts.items():
        assert abs(found[name] / want - 1) <= 0.05, f"{name}: {found[name]} vs {want}"
    # the same through the profile entry point with a rule calendar
    retail = UsRetailCalendar()
    assert set(temporal_profile(ts, retail).holiday_lifts) >= {"black_friday", "cyber_monday"}


def test_paydays_and_period_ends_are_recovered():
    start, end = date(2020, 1, 1), date(2024, 12, 31)
    spec = {
        "payday": {"kind": "biweekly", "anchor": "2020-01-03", "lift": 1.4},
        "month_end": {"lift": 1.25, "days": 2},
    }
    ts = _generate(4_000_000, 31, start, end, calendar=spec)
    pay = Payday(1.4, kind="biweekly", anchor=date(2020, 1, 3))
    pay_dates = {
        start + timedelta(days=i): "payday" for i, v in enumerate(pay.factors(start, end)) if v > 1
    }
    assert abs(holiday_lifts(ts, pay_dates, exclude_days=1)["payday"] / 1.4 - 1) <= 0.03
    month_end = PeriodEnd(1.25, "month", 2)
    me = {
        start + timedelta(days=i): "month_end"
        for i, v in enumerate(month_end.factors(start, end))
        if v > 1
    }
    got = holiday_lifts(ts, me, exclude_days=0)["month_end"]
    assert abs(got / 1.25 - 1) <= 0.03


def test_trend_shows_in_the_yearly_counts():
    start, end = date(2020, 1, 1), date(2023, 12, 31)
    ts = _generate(
        2_000_000,
        41,
        start,
        end,
        calendar={"trend": {"annual_growth": 0.2, "origin": "2020-01-01"}},
    )
    years = np.asarray(ts.to_numpy(zero_copy_only=False)).astype("datetime64[Y]").astype(int) + 1970
    counts = np.bincount(years - 2020, minlength=4).astype(float)
    ratios = counts[1:] / counts[:-1]
    assert ratios == pytest.approx([1.2] * 3, rel=0.03)
    stepped = _generate(
        1_000_000, 42, date(2024, 1, 1), date(2024, 12, 31),
        calendar={"trend": {"steps": [{"date": "2024-07-01", "factor": 3.0}]}},
    )  # fmt: skip
    months = (
        np.asarray(stepped.to_numpy(zero_copy_only=False)).astype("datetime64[M]").astype(int) % 12
    )
    first_half = (months < 6).mean()
    assert first_half == pytest.approx(181 / (181 + 3.0 * 184), rel=0.02)


def test_timestamps_are_row_addressed_bounded_and_validated():
    start, end = date(2024, 2, 1), date(2024, 3, 31)
    s = RowStream(5, "t", "c", "v")
    whole = sample_timestamps(s, 0, 30_000, start, end, month=MONTH, day_of_week=DOW)
    parts = pa.concat_arrays(
        [
            sample_timestamps(s, a, 7_000, start, end, month=MONTH, day_of_week=DOW)
            for a in range(0, 28_000, 7_000)
        ]
        + [sample_timestamps(s, 28_000, 2_000, start, end, month=MONTH, day_of_week=DOW)]
    )
    assert whole.equals(parts) and whole.type == pa.timestamp("us")
    arr = np.asarray(whole.to_numpy(zero_copy_only=False)).astype("datetime64[us]")
    assert arr.min() >= np.datetime64("2024-02-01") and arr.max() < np.datetime64("2024-04-01")
    whole_s = sample_timestamps(s, 0, 1_000, start, end, whole_seconds=True)
    sec = np.asarray(whole_s.to_numpy(zero_copy_only=False)).astype("datetime64[us]")
    assert (sec.astype("datetime64[s]").astype("datetime64[us]") == sec).all()
    assert len(sample_timestamps(s, 0, 0, start, end)) == 0
    for kw in (
        {"month": [1.0] * 11},
        {"day_of_week": [0.0] * 7},
        {"hour": [1.0] * 24, "hour_peaks": ([12], 2.0)},
        {"hour": [-1.0] + [1.0] * 23},
    ):
        with pytest.raises(ValueError):
            sample_timestamps(s, 0, 10, start, end, **kw)
    with pytest.raises(ValueError):
        sample_timestamps(s, 0, 10, end, start)
    assert len(day_probabilities(start, end)) == 60


def test_profile_of_dates_nulls_and_errors():
    dates = pa.array([date(2024, 1, 1), None, date(2024, 1, 2), date(2024, 1, 8)], type=pa.date32())
    p = temporal_profile(dates)
    assert p.n == 3 and p.hour is None
    assert p.day_of_week[0] == pytest.approx(2 / 3)  # two Mondays
    assert p.month[0] == 1.0
    with pytest.raises(ValueError):
        temporal_profile(pa.array([1, 2, 3]))
    with pytest.raises(ValueError):
        temporal_profile(pa.array([None], type=pa.timestamp("us")))
    ns = pa.array([np.datetime64("2024-03-05T10:30:00", "ns")], type=pa.timestamp("ns"))
    assert temporal_profile(ns).hour[10] == 1.0  # type: ignore[index]


def test_tail_index():
    pareto = family_by_name("pareto").sample(
        RowStream(1, "t", "p", "v"), 0, 1_000_000, {"alpha": 1.5, "xm": 1.0}
    )
    assert tail_index(pareto) == pytest.approx(1.5, rel=0.05)
    assert tail_index(
        family_by_name("pareto").sample(
            RowStream(2, "t", "p", "v"), 0, 1_000_000, {"alpha": 3.0, "xm": 1.0}
        )
    ) == pytest.approx(3.0, rel=0.05)
    thin = (
        family_by_name("exponential").sample(
            RowStream(3, "t", "e", "v"), 0, 1_000_000, {"lam": 1.0}
        )
        + 1.0
    )
    heavy_idx = tail_index(pareto) or 0
    assert (tail_index(thin) or 0) > 2 * heavy_idx
    assert tail_index([1.0, 2.0, 3.0]) is None
    assert tail_index([-1.0] * 100) is None
    assert tail_index([5.0] * 100) is None
