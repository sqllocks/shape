"""W3-10: time-series quality checks: gaps, stuck values and daylight-saving transitions."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

import pyarrow as pa
import pytest

from shape.cli.main import main
from shape.quality import (
    TimeSeriesGate,
    ValidationContext,
    VerifyConfig,
    VerifyConfigError,
    VerifyRunner,
    check_timeseries,
    validate_timeseries_rules,
)
from shape.quality.timeseries import parse_every

T0 = datetime(2026, 1, 1)


def hourly(n, skip=(), start=T0):
    return [start + timedelta(hours=i) for i in range(n) if i not in skip]


def table(times, values=None, sensors=None, tz=None):
    cols = {"ts": pa.array(times, pa.timestamp("us", tz=tz))}
    if values is not None:
        cols["v"] = pa.array(values)
    if sensors is not None:
        cols["sensor"] = pa.array(sensors)
    return pa.table(cols)


def run(t, **rule):
    rule = {"table": "r", "time": "ts", **rule}
    return check_timeseries({"r": t}, [rule])


def errors(findings):
    return [f for f in findings if f["severity"] == "error"]


# ---- the interval syntax


@pytest.mark.parametrize(
    "text, micros",
    [
        ("500ms", 500_000),
        ("30s", 30_000_000),
        ("15min", 900_000_000),
        ("1h", 3_600_000_000),
        ("1d", 86_400_000_000),
        ("2w", 14 * 86_400_000_000),
        ("250us", 250),
    ],
)
def test_every_parses_the_documented_units(text, micros):
    assert parse_every(text) == micros


@pytest.mark.parametrize("bad", ["", "h", "1", "0h", "-1h", "1.5h", "1month", "1 h", 3600, None])
def test_every_refuses_what_is_not_an_interval(bad):
    with pytest.raises(ValueError, match="every"):
        parse_every(bad)


# ---- gaps


def test_a_complete_series_has_no_gap():
    assert run(table(hourly(48)), every="1h", gaps={}) == []


def test_missing_steps_are_counted_and_located():
    found = run(table(hourly(10, skip=(3, 4, 7))), every="1h", gaps={})
    assert len(errors(found)) == 1
    f = found[0]
    assert f["rule"] == "timeseries.gaps" and f["table"] == "r" and f["column"] == "ts"
    assert f["observed"]["missing"] == 3
    assert f["observed"]["gaps"] == [
        {"after": "2026-01-01T02:00:00", "before": "2026-01-01T05:00:00", "missing": 2},
        {"after": "2026-01-01T06:00:00", "before": "2026-01-01T08:00:00", "missing": 1},
    ]


def test_the_gap_tolerance_boundary_is_inclusive():
    t = table(hourly(10, skip=(3, 4, 7)))
    assert run(t, every="1h", gaps={"max_missing": 3}) == []
    assert len(errors(run(t, every="1h", gaps={"max_missing": 2}))) == 1


def test_gaps_are_found_per_group_and_the_input_order_does_not_matter():
    a = hourly(6)
    b = hourly(6, skip=(2,))
    times = a + b
    sensors = ["a"] * len(a) + ["b"] * len(b)
    t = table(times, sensors=sensors).take(list(reversed(range(len(times)))))
    found = errors(run(t, every="1h", by=["sensor"], gaps={}))
    assert len(found) == 1
    assert found[0]["observed"]["missing"] == 1
    assert found[0]["observed"]["gaps"][0]["group"] == {"sensor": "b"}
    # pooled, the groups cover each other's hours: no gap
    assert errors(run(t, every="1h", gaps={})) == []


def test_a_series_with_one_row_or_none_has_no_gap():
    assert run(table(hourly(1)), every="1h", gaps={}) == []
    assert run(table([]), every="1h", gaps={}) == []


def test_duplicates_and_off_grid_steps_warn_and_do_not_fail():
    times = [T0, T0 + timedelta(hours=1), T0 + timedelta(hours=1), T0 + timedelta(minutes=150)]
    found = run(table(times), every="1h", gaps={})
    assert errors(found) == []
    kinds = {f["rule"] for f in found}
    assert kinds == {"timeseries.duplicates", "timeseries.off_grid"}


def test_null_times_are_reported_as_a_warning():
    times = hourly(4) + [None]
    found = run(table(times), every="1h", gaps={})
    assert errors(found) == []
    assert [f["rule"] for f in found] == ["timeseries.null_times"]
    assert found[0]["observed"] == {"null_times": 1}


def test_iso_text_times_are_read():
    t = pa.table({"ts": [x.isoformat() for x in hourly(5, skip=(2,))]})
    assert run(t, every="1h", gaps={})[0]["observed"]["missing"] == 1


def test_a_time_column_that_is_not_a_time_is_an_error():
    f = run(pa.table({"ts": ["yesterday", "today"]}), every="1h", gaps={})
    assert f[0]["severity"] == "error" and f[0]["rule"] == "timeseries.time_column"
    f = run(pa.table({"ts": [1, 2]}), every="1h", gaps={})
    assert f[0]["rule"] == "timeseries.time_column"


def test_missing_table_or_column_is_an_error_not_a_crash():
    f = check_timeseries({}, [{"table": "r", "time": "ts", "every": "1h", "gaps": {}}])
    assert f[0]["rule"] == "timeseries.table_exists"
    f = check_timeseries(
        {"r": pa.table({"x": [1]})}, [{"table": "r", "time": "ts", "every": "1h", "gaps": {}}]
    )
    assert f[0]["rule"] == "timeseries.column_exists"


def test_aware_timestamps_use_the_instant():
    t = table(hourly(6, skip=(2,), start=datetime(2026, 1, 1, tzinfo=UTC)), tz="UTC")
    assert run(t, every="1h", gaps={})[0]["observed"]["missing"] == 1


# ---- stuck values


def test_a_run_longer_than_the_limit_is_stuck():
    t = table(hourly(8), values=[1, 2, 2, 2, 2, 3, 4, 5])
    found = errors(run(t, stuck={"column": "v", "max_run": 3}))
    assert len(found) == 1 and found[0]["rule"] == "timeseries.stuck"
    assert found[0]["column"] == "v"
    assert found[0]["observed"]["runs"] == [
        {"start": "2026-01-01T01:00:00", "end": "2026-01-01T04:00:00", "length": 4, "value": 2}
    ]


def test_the_stuck_limit_boundary_allows_a_run_of_exactly_max_run():
    t = table(hourly(8), values=[1, 2, 2, 2, 2, 3, 4, 5])
    assert run(t, stuck={"column": "v", "max_run": 4}) == []
    assert len(errors(run(t, stuck={"column": "v", "max_run": 3}))) == 1


def test_nulls_and_nans_break_a_run():
    nan = float("nan")
    t = table(hourly(7), values=[1.0, 1.0, None, 1.0, 1.0, nan, nan])
    assert run(t, stuck={"column": "v", "max_run": 2}) == []


def test_stuck_runs_do_not_cross_groups_and_follow_time_order():
    times = hourly(3) + hourly(3)
    sensors = ["a"] * 3 + ["b"] * 3
    values = [5, 5, 9, 5, 9, 9]
    t = table(times, values=values, sensors=sensors)
    assert run(t, by=["sensor"], stuck={"column": "v", "max_run": 2}) == []
    found = errors(run(t, by=["sensor"], stuck={"column": "v", "max_run": 1}))
    assert {tuple(r["group"].items()) for r in found[0]["observed"]["runs"]} == {
        (("sensor", "a"),),
        (("sensor", "b"),),
    }
    # in time order the three 7s are adjacent although the rows were stored apart
    shuffled = table(hourly(4), values=[7, 1, 7, 7]).take([0, 2, 3, 1])
    assert len(errors(run(shuffled, stuck={"column": "v", "max_run": 2}))) == 0
    assert (
        len(errors(run(table(hourly(4), values=[7, 7, 7, 1]), stuck={"column": "v", "max_run": 2})))
        == 1
    )


def test_stuck_works_on_text_values():
    t = table(hourly(4), values=["ok", "ok", "ok", "bad"])
    assert len(errors(run(t, stuck={"column": "v", "max_run": 2}))) == 1


# ---- daylight saving (Europe/Berlin: 2026-03-29 02:00 -> 03:00, 2026-10-25 03:00 -> 02:00)

BERLIN = {"time_zone": "Europe/Berlin"}
SPRING = datetime(2026, 3, 28, 22)
FALL = datetime(2026, 10, 24, 22)


def local_hours(start, n):
    return [start + timedelta(hours=i) for i in range(n)]


def spring_series(*, with_missing_hour):
    times = local_hours(SPRING, 8)  # 22:00 .. 05:00 local
    if with_missing_hour:
        times = [t for t in times if t.hour != 2]
    return times


def test_the_hour_that_does_not_exist_is_not_a_gap_when_the_zone_is_given():
    t = table(spring_series(with_missing_hour=True))
    assert run(t, every="1h", gaps={}, dst=BERLIN) == []
    # without the zone the same data has a missing hour
    assert run(t, every="1h", gaps={})[0]["observed"]["missing"] == 1


def test_rows_in_the_skipped_hour_are_reported():
    found = errors(run(table(spring_series(with_missing_hour=False)), every="1h", dst=BERLIN))
    assert len(found) == 1 and found[0]["rule"] == "timeseries.dst"
    assert found[0]["observed"]["nonexistent_local_times"] == {
        "transition": "2026-03-29T02:00:00",
        "rows": 1,
    }


def fall_series(*, repeat):
    times = local_hours(FALL, 4)  # 22:00 23:00 00:00 01:00 local
    times += [datetime(2026, 10, 25, 2)] * (2 if repeat else 1)
    times += [datetime(2026, 10, 25, 3), datetime(2026, 10, 25, 4)]
    return sorted(times)


def test_the_repeated_hour_is_expected_twice_by_default():
    assert run(table(fall_series(repeat=True)), every="1h", dst=BERLIN) == []
    found = errors(run(table(fall_series(repeat=False)), every="1h", dst=BERLIN))
    assert len(found) == 1
    obs = found[0]["observed"]["repeated_local_hour"]
    assert obs == {"transition": "2026-10-25T02:00:00", "expected": 2, "wrong": 1}


def test_the_repeated_hour_can_be_expected_once():
    rule = {"time_zone": "Europe/Berlin", "fall_back": "once"}
    assert run(table(fall_series(repeat=False)), every="1h", dst=rule) == []
    assert len(errors(run(table(fall_series(repeat=True)), every="1h", dst=rule))) == 1


def test_a_series_that_does_not_cross_a_transition_is_not_checked():
    assert run(table(hourly(24)), every="1h", gaps={}, dst=BERLIN) == []


def test_a_zone_without_daylight_saving_has_no_transition():
    t = table(spring_series(with_missing_hour=False))
    assert run(t, every="1h", dst={"time_zone": "UTC"}) == []


def test_aware_times_are_checked_in_the_zone():
    instants = [datetime(2026, 3, 28, 21, tzinfo=UTC) + timedelta(hours=i) for i in range(8)]
    t = table(instants, tz="UTC")
    assert run(t, every="1h", gaps={}, dst=BERLIN) == []


# ---- rule validation


@pytest.mark.parametrize(
    "rules, message",
    [
        ("x", "must be a list"),
        (["x"], "timeseries[0]: must be an object"),
        ([{"time": "ts", "every": "1h", "gaps": {}}], 'missing required key "table"'),
        ([{"table": "r", "every": "1h", "gaps": {}}], 'missing required key "time"'),
        ([{"table": "r", "time": "ts", "gaps": {}}], 'missing required key "every"'),
        ([{"table": "r", "time": "ts", "every": "1h"}], "needs at least one of"),
        ([{"table": "r", "time": "ts", "every": "1h", "gaps": {}, "gapz": {}}], "unknown key"),
        ([{"table": "r", "time": "ts", "every": "1 h", "gaps": {}}], "every"),
        ([{"table": "r", "time": "ts", "every": "1h", "gaps": {"max_missing": -1}}], "max_missing"),
        (
            [{"table": "r", "time": "ts", "every": "1h", "gaps": {"max_missing": 1.5}}],
            "max_missing",
        ),
        ([{"table": "r", "time": "ts", "every": "1h", "gaps": {"x": 1}}], "unknown key"),
        ([{"table": "r", "time": "ts", "stuck": {"max_run": 3}}], 'missing required key "column"'),
        (
            [{"table": "r", "time": "ts", "stuck": {"column": "v"}}],
            'missing required key "max_run"',
        ),
        ([{"table": "r", "time": "ts", "stuck": {"column": "v", "max_run": 0}}], "max_run"),
        (
            [{"table": "r", "time": "ts", "by": "sensor", "stuck": {"column": "v", "max_run": 2}}],
            "by",
        ),
        ([{"table": "r", "time": "ts", "every": "1h", "dst": {}}], '"time_zone"'),
        (
            [{"table": "r", "time": "ts", "every": "1h", "dst": {"time_zone": "Mars/Base"}}],
            "time zone",
        ),
        (
            [
                {
                    "table": "r",
                    "time": "ts",
                    "every": "1h",
                    "dst": {"time_zone": "UTC", "fall_back": "x"},
                }
            ],
            "fall_back",
        ),
        (
            [{"table": "r", "time": "ts", "dst": {"time_zone": "UTC"}}],
            'missing required key "every"',
        ),
    ],
)
def test_bad_rules_are_refused_with_the_key_named(rules, message):
    with pytest.raises(ValueError, match=re.escape(message)):
        validate_timeseries_rules(rules)


# ---- the gate and the verify configuration


def doc(**rules):
    return {"format": "shape-verify-config", "version": 1, **rules}


RULE = {"table": "r", "time": "ts", "every": "1h", "gaps": {"max_missing": 0}}


def test_the_gate_reports_the_findings_as_errors_and_warnings():
    t = table(hourly(10, skip=(3,)) + [None])
    ctx = ValidationContext(tables={"r": t}, config={"timeseries": [RULE]})
    result = TimeSeriesGate().check(ctx)
    assert result.gate_name == "timeseries_quality" and not result.passed
    assert "r.ts: 1 missing step" in result.errors[0]
    assert any("no time" in w for w in result.warnings)
    assert "timeseries.gaps" in [f["rule"] for f in result.details["findings"]]


def test_the_gate_with_no_rules_passes_and_checks_nothing():
    assert TimeSeriesGate().check(ValidationContext()).passed


def test_verify_config_runs_the_gate_and_refuses_bad_rules(tmp_path):
    cfg = VerifyConfig.from_dict(doc(timeseries=[RULE]))
    res = VerifyRunner(None, config=cfg).run({"r": table(hourly(10, skip=(3,)))})
    assert [g.gate_name for g in res.gate_results] == ["timeseries_quality"]
    assert not res.passed
    with pytest.raises(VerifyConfigError, match=r"timeseries\[0\]"):
        VerifyConfig.from_dict(doc(timeseries=[{"table": "r"}]))


def test_the_cli_runs_the_timeseries_gate(tmp_path, capsys):
    import pyarrow.parquet as pq

    d = tmp_path / "d"
    d.mkdir()
    pq.write_table(table(hourly(10, skip=(3,))), d / "r.parquet")
    cfg = tmp_path / "v.json"
    cfg.write_text(json.dumps(doc(timeseries=[RULE])))
    assert main(["verify", str(d), "--config", str(cfg)]) == 1
    out = capsys.readouterr()
    assert "timeseries_quality" in out.out and "1 missing step" in out.err
    cfg.write_text(json.dumps(doc(timeseries=[{**RULE, "gaps": {"max_missing": 1}}])))
    assert main(["verify", str(d), "--config", str(cfg)]) == 0


# -- HUNT2-quality ----------------------------------------------------------------------------


def test_stuck_check_on_a_column_that_cannot_be_compared_is_a_finding():
    """#576: the Arrow error was swallowed and the check passed."""
    import datetime as dt

    import pyarrow as pa

    from shape.quality.timeseries import check_timeseries

    ts = pa.array([dt.datetime(2024, 1, 1, h) for h in range(4)], pa.timestamp("us"))
    rule = {"table": "t", "time": "ts", "stuck": {"column": "v", "max_run": 1}}
    bad = check_timeseries({"t": pa.table({"ts": ts, "v": [{"a": 1}] * 4})}, [rule])
    assert [f["rule"] for f in bad] == ["timeseries.column_type"]
    assert bad[0]["severity"] == "error" and bad[0]["column"] == "v"
    ok = check_timeseries({"t": pa.table({"ts": ts, "v": [1, 2, 3, 4]})}, [rule])
    assert ok == []
    stuck = check_timeseries({"t": pa.table({"ts": ts, "v": [1, 1, 1, 1]})}, [rule])
    assert [f["rule"] for f in stuck] == ["timeseries.stuck"]
