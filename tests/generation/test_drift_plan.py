"""ISS-diff #14: drift planted over time (step, ramp, window) with an answer key, and the diff
finding it."""

from __future__ import annotations

import datetime as dt
import json
import math

import pytest

import shape
from shape.generation.drift_plan import DriftEvent, DriftPlan, DriftPlanError
from shape.generation.schema import GenSchema

DOC = {
    "schema_version": 1,
    "model": {"name": "feed", "seed": 7, "schema_mode": "3nf"},
    "tables": {
        "orders": {
            "name": "orders",
            "primary_key": ["order_id"],
            "columns": {
                "order_id": {
                    "name": "order_id",
                    "type": "integer",
                    "generator": {"strategy": "sequence"},
                },
                "status": {
                    "name": "status",
                    "type": "string",
                    "generator": {
                        "strategy": "weighted_enum",
                        "values": {"completed": 80, "shipped": 15, "cancelled": 5},
                    },
                },
                "total": {
                    "name": "total",
                    "type": "float",
                    "generator": {
                        "strategy": "distribution",
                        "distribution": "log_normal",
                        "mean": 4.0,
                        "sigma": 0.5,
                    },
                },
                "note": {
                    "name": "note",
                    "type": "string",
                    "nullable": True,
                    "null_rate": 0.02,
                    "generator": {"strategy": "weighted_enum", "values": {"a": 1, "b": 1}},
                },
                "vip": {
                    "name": "vip",
                    "type": "integer",
                    "generator": {
                        "strategy": "distribution",
                        "distribution": "bernoulli",
                        "probability": 0.5,
                    },
                },
            },
        }
    },
    "generation": {"scale": "small", "scales": {"small": {"orders": 3000}}},
}
SCHEMA = GenSchema.from_dict(DOC)


def plan(events, days=14):
    return DriftPlan(events, start="2026-03-01", days=days)


def profile_of(p, day, rows=3000):
    result = p.generate_day(SCHEMA, day, row_counts={"orders": rows})
    return shape.profile(result["orders"], name="orders")


EVENTS = [
    {
        "kind": "null_rate",
        "table": "orders",
        "column": "note",
        "start": "2026-03-04",
        "ramp_days": 4,
        "to": 0.4,
    },
    {
        "kind": "new_category",
        "table": "orders",
        "column": "status",
        "start": "2026-03-06",
        "value": "lost",
        "share": 0.08,
    },
    {
        "kind": "distribution",
        "table": "orders",
        "column": "total",
        "start": "2026-03-08",
        "scale": 1.4,
    },
    {
        "kind": "add_column",
        "table": "orders",
        "column": "channel",
        "start": "2026-03-10",
        "definition": {
            "type": "string",
            "generator": {"strategy": "weighted_enum", "values": {"web": 3, "store": 1}},
        },
    },
    {
        "kind": "type_change",
        "table": "orders",
        "column": "vip",
        "start": "2026-03-11",
        "end": "2026-03-13",
        "to": {
            "type": "string",
            "generator": {"strategy": "weighted_enum", "values": {"yes": 1, "no": 1}},
        },
    },
]


def kinds(diff):
    return {(c["column"], c["kind"]) for c in diff.changes}


def test_every_planted_event_is_found_by_the_diff():
    p = plan(EVENTS)
    quiet = profile_of(p, 0)
    # before anything happens, two days of the feed do not drift
    assert not {
        k for k in kinds(shape.diff(quiet, profile_of(p, 1))) if k[1] != "distribution_change"
    }
    found = {}
    for day in (10, 11):  # every event is in effect: the type window is days 10 and 11
        found[day] = kinds(shape.diff(quiet, profile_of(p, day)))
    got = found[10]
    assert ("note", "null_rate_change") in got
    assert ("status", "new_categorical_values") in got
    assert ("total", "mean_shift") in got and ("total", "distribution_shift") in got
    assert ("channel", "column_added") in got
    assert ("vip", "dtype_change") in got
    # the window is over on day 12: the type is back, the other changes stay
    after = kinds(shape.diff(quiet, profile_of(p, 12)))
    assert ("vip", "dtype_change") not in after and ("channel", "column_added") in after


def test_expected_changes_match_what_the_diff_reports():
    p = plan(EVENTS)
    a, b = 0, 10
    expected = p.expected_changes(a, b)
    assert {e["event"] for e in expected} == {"e1", "e2", "e3", "e4", "e5"}
    got = kinds(shape.diff(profile_of(p, a), profile_of(p, b)))
    for e in expected:
        assert any((e["column"], k) in got for k in e["kinds"]), e
    # between two quiet days nothing is expected
    assert p.expected_changes(0, 2) == []
    # the window event differs between day 11 and day 12, nothing else does
    assert [e["event"] for e in p.expected_changes(11, 12)] == ["e5"]


def test_a_ramp_builds_up_and_then_holds():
    p = plan([EVENTS[0]])
    rates = [p.schema_at(SCHEMA, d).tables["orders"].columns["note"].null_rate for d in range(10)]
    assert rates[:3] == [0.02, 0.02, 0.02]
    assert rates[3] == pytest.approx(0.02 + 0.25 * 0.38)
    assert rates[6] == pytest.approx(0.4) and rates[9] == pytest.approx(0.4)
    assert all(b >= a for a, b in zip(rates, rates[1:], strict=False))


def test_a_window_reverts_the_schema_exactly():
    p = plan(
        [
            {
                "kind": "null_rate",
                "table": "orders",
                "column": "note",
                "start": 2,
                "end": 4,
                "to": 0.5,
            }
        ]
    )
    assert p.schema_at(SCHEMA, 1).to_dict() == SCHEMA.to_dict()
    assert p.schema_at(SCHEMA, 2).tables["orders"].columns["note"].null_rate == 0.5
    assert p.schema_at(SCHEMA, 4).to_dict() == SCHEMA.to_dict()


def test_a_new_category_is_absent_from_the_schema_until_its_day():
    p = plan([EVENTS[1]])
    before = p.schema_at(SCHEMA, 4).tables["orders"].columns["status"].generator["values"]
    assert (
        "lost" not in before
        and before == DOC["tables"]["orders"]["columns"]["status"]["generator"]["values"]
    )
    day = p.schema_at(SCHEMA, 5).tables["orders"].columns["status"].generator["values"]
    assert day["lost"] == pytest.approx(0.08) and sum(day.values()) == pytest.approx(1.0)
    assert day["completed"] == pytest.approx(0.8 * 0.92)
    values = set(p.generate_day(SCHEMA, 5)["orders"]["status"].to_pylist())
    assert "lost" in values
    assert "lost" not in set(p.generate_day(SCHEMA, 4)["orders"]["status"].to_pylist())


def test_category_weights_move_the_mix():
    p = plan(
        [
            {
                "kind": "category_weights",
                "table": "orders",
                "column": "status",
                "start": 3,
                "weights": {"completed": 40, "shipped": 40, "cancelled": 20},
            }
        ]
    )
    d = shape.diff(profile_of(p, 0), profile_of(p, 5))
    assert ("status", "category_shift") in kinds(d) and (
        "status",
        "new_categorical_values",
    ) not in kinds(d)


def test_scale_step_on_a_log_normal_adds_ln_of_the_factor():
    p = plan([EVENTS[2]])
    gen = p.schema_at(SCHEMA, 7).tables["orders"].columns["total"].generator
    assert gen["mean"] == pytest.approx(4.0 + math.log(1.4)) and gen["sigma"] == 0.5
    assert p.schema_at(SCHEMA, 6).tables["orders"].columns["total"].generator["mean"] == 4.0
    p2 = plan(
        [
            {
                "kind": "distribution",
                "table": "orders",
                "column": "total",
                "start": 0,
                "params": {"sigma": {"factor": 2}, "mean": 3.0},
            }
        ]
    )
    gen2 = p2.schema_at(SCHEMA, 0).tables["orders"].columns["total"].generator
    assert (gen2["sigma"], gen2["mean"]) == (1.0, 3.0)


def test_a_boolean_rate_can_drift_too():
    p = plan(
        [
            {
                "kind": "distribution",
                "table": "orders",
                "column": "vip",
                "start": 3,
                "params": {"probability": 0.95},
            }
        ]
    )
    d = shape.diff(profile_of(p, 0), profile_of(p, 4))
    assert ("vip", "true_rate_change") in kinds(d)


def test_the_original_schema_is_never_changed():
    before = json.dumps(SCHEMA.to_dict(), sort_keys=True)
    p = plan(EVENTS)
    for d in range(14):
        p.schema_at(SCHEMA, d)
    assert json.dumps(SCHEMA.to_dict(), sort_keys=True) == before


def test_days_are_reproducible_and_differ():
    p = plan(EVENTS[:2])
    a, b = p.generate_day(SCHEMA, 2)["orders"], p.generate_day(SCHEMA, 2)["orders"]
    assert a.equals(b)
    assert not a["status"].equals(p.generate_day(SCHEMA, 3)["orders"]["status"])


def test_ground_truth_lists_every_event_and_what_was_active_each_day():
    p = plan(EVENTS)
    truth = p.ground_truth()
    assert truth["version"] == 1 and truth["start"] == "2026-03-01" and truth["days"] == 14
    by_id = {e["id"]: e for e in truth["events"]}
    assert set(by_id) == {"e1", "e2", "e3", "e4", "e5"}
    assert by_id["e1"]["shape"] == "ramp" and by_id["e1"]["full_effect_from"] == "2026-03-07"
    assert by_id["e2"]["shape"] == "step" and by_id["e5"]["shape"] == "window"
    assert by_id["e5"]["end"] == "2026-03-13" and by_id["e5"]["detected_as"] == ["dtype_change"]
    day = {d["date"]: d["events"] for d in truth["by_day"]}
    assert day["2026-03-01"] == {} and day["2026-03-05"] == {"e1": 0.5}
    assert (
        set(day["2026-03-12"]) == {"e1", "e2", "e3", "e4", "e5"} and "e5" not in day["2026-03-13"]
    )
    json.dumps(truth)  # JSON-ready


def test_write_files_specs_and_the_answer_key(tmp_path):
    early = [{**EVENTS[0], "start": "2026-03-01", "ramp_days": 0}]
    p = DriftPlan(early, start="2026-03-01", days=3)
    files = p.write(SCHEMA, tmp_path, row_counts={"orders": 200}, fmt="csv")
    assert (tmp_path / "2026-03-02" / "orders.csv").exists()
    assert json.loads((tmp_path / "ground_truth.json").read_text())["days"] == 3
    spec = json.loads((tmp_path / "_specs" / "2026-03-03.json").read_text())
    assert GenSchema.from_dict(spec).tables["orders"].columns["note"].null_rate == 0.4
    assert len(files) == 3 * 2 + 1


def test_from_dict_and_load(tmp_path):
    doc = {"start": "2026-03-01", "days": 10, "events": EVENTS[:2]}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(doc))
    assert [e.id for e in DriftPlan.load(path).events] == ["", ""]
    assert DriftPlan.from_dict(doc).active(dt.date(2026, 3, 6)) == ["e1", "e2"]


@pytest.mark.parametrize(
    "event, match",
    [
        ({"kind": "bogus", "table": "orders", "column": "note", "start": 1}, "unknown event kind"),
        ({"kind": "null_rate", "table": "orders", "column": "note", "start": 1}, "needs 'to'"),
        (
            {"kind": "null_rate", "table": "orders", "column": "note", "start": 1, "to": 2},
            "between 0 and 1",
        ),
        (
            {
                "kind": "null_rate",
                "table": "orders",
                "column": "note",
                "start": 3,
                "end": 2,
                "to": 0.1,
            },
            "end must be after",
        ),
        (
            {
                "kind": "drop_column",
                "table": "orders",
                "column": "note",
                "start": 1,
                "ramp_days": 3,
            },
            "no ramp",
        ),
        ({"kind": "null_rate", "column": "note", "start": 1, "to": 0.1}, "needs"),
        (
            {
                "kind": "null_rate",
                "table": "orders",
                "column": "note",
                "start": "yesterday",
                "to": 0.1,
            },
            "ISO date",
        ),
    ],
)
def test_bad_events_are_errors(event, match):
    with pytest.raises(DriftPlanError, match=match):
        plan([event])


@pytest.mark.parametrize(
    "event, match",
    [
        (
            {"kind": "null_rate", "table": "nope", "column": "note", "start": 0, "to": 0.1},
            "no table",
        ),
        (
            {"kind": "null_rate", "table": "orders", "column": "nope", "start": 0, "to": 0.1},
            "no column",
        ),
        (
            {"kind": "drop_column", "table": "orders", "column": "order_id", "start": 0},
            "primary key",
        ),
        (
            {
                "kind": "category_weights",
                "table": "orders",
                "column": "total",
                "start": 0,
                "weights": {"a": 1},
            },
            "weighted_enum",
        ),
        (
            {"kind": "distribution", "table": "orders", "column": "status", "start": 0, "scale": 2},
            "generated by",
        ),
        (
            {"kind": "distribution", "table": "orders", "column": "vip", "start": 0, "scale": 2},
            "not defined",
        ),
        (
            {
                "kind": "distribution",
                "table": "orders",
                "column": "total",
                "start": 0,
                "params": {"nope": {"factor": 2}},
            },
            "not in the generator",
        ),
    ],
)
def test_events_the_schema_cannot_take_are_errors(event, match):
    with pytest.raises(DriftPlanError, match=match):
        plan([event]).schema_at(SCHEMA, 1)


def test_the_day_must_be_inside_the_plan():
    p = plan(EVENTS, days=3)
    with pytest.raises(DriftPlanError, match="outside the plan"):
        p.schema_at(SCHEMA, 3)
    assert DriftEvent.from_dict(EVENTS[0]).ramp_days == 4


def test_cli_generate_drift_and_diff_the_days(tmp_path, capsys):
    from shape.cli.main import main

    schema_file = tmp_path / "feed.gen.json"
    schema_file.write_text(json.dumps(DOC))
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps({"start": "2026-03-01", "days": 6, "events": EVENTS[:2]}))
    out = tmp_path / "out"
    argv = ["generate-drift", str(schema_file), str(plan_file), "-o", str(out)]
    assert main([*argv, "--rows", "orders=2500", "--format", "csv", "--days", "7"]) == 0
    text = capsys.readouterr().out
    assert "Planted 2 events over 7 days" in text and "e2: new_category" in text
    truth = json.loads((out / "ground_truth.json").read_text())
    assert truth["days"] == 7 and [e["id"] for e in truth["events"]] == ["e1", "e2"]
    first, last = out / "2026-03-01" / "orders.csv", out / "2026-03-07" / "orders.csv"
    assert first.exists() and last.exists() and (out / "_specs" / "2026-03-07.json").exists()
    a, b = shape.profile(first, name="orders"), shape.profile(last, name="orders")
    got = kinds(shape.diff(a, b))
    assert ("note", "null_rate_change") in got and ("status", "new_categorical_values") in got
    capsys.readouterr()
    assert main([*argv, "--rows", "nope=3"]) == 2
    assert main([*argv, "--rows", "orders"]) == 2
    plan_file.write_text(json.dumps({"events": [{"kind": "bogus"}]}))
    assert main(argv) == 2
