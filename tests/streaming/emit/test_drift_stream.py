"""Drift plans in a stream (W2-09 item 5): ``--drift-plan``, ``--rows``, ``--day-seconds`` and the
drift records of the answer key."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from shape.cli.main import main
from shape.generation.drift_plan import DriftPlan
from shape.generation.schema import GenSchema
from shape.streaming.emit import (
    AnswerKey,
    EmitConfig,
    EmitRunner,
    MemorySink,
    read_answer_key,
)
from shape.streaming.emit.drift import DriftEventPlan
from shape.streaming.emit.formats import FIELD_SEQ, FIELD_TABLE, FIELD_TIME, rows_of
from shape.streaming.emit.rate import DaySchedule

SCHEMA_DOC = {
    "schema_version": 1,
    "model": {"name": "feed", "seed": 7, "schema_mode": "3nf"},
    "tables": {
        "customers": {
            "name": "customers",
            "primary_key": ["customer_id"],
            "columns": {
                "customer_id": {
                    "name": "customer_id",
                    "type": "integer",
                    "generator": {"strategy": "sequence"},
                },
                "tier": {
                    "name": "tier",
                    "type": "string",
                    "generator": {
                        "strategy": "weighted_enum",
                        "values": {"gold": 10, "silver": 30, "bronze": 60},
                    },
                },
            },
        },
        "orders": {
            "name": "orders",
            "primary_key": ["order_id"],
            "columns": {
                "order_id": {
                    "name": "order_id",
                    "type": "integer",
                    "generator": {"strategy": "sequence"},
                },
                "placed": {
                    "name": "placed",
                    "type": "timestamp",
                    "generator": {
                        "strategy": "temporal",
                        "pattern": "uniform",
                        "start": "2024-01-01",
                        "end": "2024-03-31",
                    },
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
            },
        },
    },
    "generation": {"scale": "small", "scales": {"small": {"customers": 40, "orders": 120}}},
}
PLAN_DOC = {
    "start": "2026-03-01",
    "days": 5,
    "events": [
        {"id": "nulls", "kind": "null_rate", "table": "orders", "column": "note",
         "to": 0.6, "start": 1, "ramp_days": 3},
        {"id": "mix", "kind": "category_weights", "table": "orders", "column": "status",
         "weights": {"completed": 20, "shipped": 20, "cancelled": 60}, "start": 2},
        {"id": "tiers", "kind": "new_category", "table": "customers", "column": "tier",
         "value": "platinum", "share": 0.2, "start": 3, "end": 5},
    ],
}  # fmt: skip
ROWS = {"customers": 40, "orders": 120}
DAY_ROWS = sum(ROWS.values())  # 160 events a day


@pytest.fixture()
def files(tmp_path: Path) -> dict[str, Path]:
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps(SCHEMA_DOC))
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(PLAN_DOC, indent=2))
    return {"schema": schema, "plan": plan, "out": tmp_path / "events.jsonl"}


def drift_args(f: dict[str, Path], *extra: str) -> list[str]:
    return [
        "emit", str(f["schema"]), "--drift-plan", str(f["plan"]),
        "--rows", "customers=40", "--rows", "orders=120", *extra,
    ]  # fmt: skip


def plan_objects(tmp_path_schema=SCHEMA_DOC, plan_doc=PLAN_DOC, **kw: Any):
    schema = GenSchema.from_dict(tmp_path_schema)
    plan = DriftPlan.from_dict(plan_doc)
    return schema, plan, DriftEventPlan(schema, plan, row_counts=ROWS, **kw)


def run_to_memory(plan: DriftEventPlan, **cfg: Any) -> MemorySink:
    sink = MemorySink()
    EmitRunner(plan, sink, EmitConfig(**cfg)).run()
    return sink


def all_rows(sink: MemorySink) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for b in sink.batches:
        rows.extend(rows_of(b))
    return rows


# ---- the events of a day are the day's generate-drift tables ---------------------------------


def test_the_events_of_day_d_equal_that_days_generate_drift_tables():
    schema, plan, ev = plan_objects()
    assert ev.total_events == 5 * DAY_ROWS and ev.day_events == [DAY_ROWS] * 5
    rows = all_rows(run_to_memory(ev))
    assert len(rows) == 5 * DAY_ROWS
    for d in range(5):
        day = rows[d * DAY_ROWS : (d + 1) * DAY_ROWS]
        tables = plan.generate_day(schema, d, row_counts=ROWS).tables
        for name, tab in tables.items():
            events = [r for r in day if r[FIELD_TABLE] == name]
            expect = rows_of(tab.combine_chunks().to_batches()[0])
            got = [{k: v for k, v in r.items() if not k.startswith("_shape_")} for r in events]
            assert got == expect, (d, name)


def test_the_sequence_continues_across_days_and_no_event_field_is_added():
    schema, plan, ev = plan_objects()
    rows = all_rows(run_to_memory(ev))
    for table, per_day in ROWS.items():
        seqs = [r[FIELD_SEQ] for r in rows if r[FIELD_TABLE] == table]
        assert seqs == list(range(5 * per_day)), table  # one run of keys, never repeated
    keys = [(r[FIELD_TABLE], r[FIELD_SEQ]) for r in rows]
    assert len(set(keys)) == len(keys)
    # the fields are the day's columns and the three event fields: nothing else was added
    columns = {t: set(tab.columns) for t, tab in schema.tables.items()}
    for r in rows:
        extra = set(r) - columns[r[FIELD_TABLE]] - {FIELD_TABLE, FIELD_SEQ, FIELD_TIME}
        assert not extra, extra
    assert FIELD_TIME in next(r for r in rows if r[FIELD_TABLE] == "orders")


def test_days_come_in_order_and_tables_in_dependency_order_within_a_day():
    _, _, ev = plan_objects()
    sink = run_to_memory(ev)
    seen = [
        (b.column(FIELD_TABLE)[0].as_py(), b.column(FIELD_SEQ)[0].as_py()) for b in sink.batches
    ]
    per_table: dict[str, list[int]] = {}
    for t, s in seen:
        per_table.setdefault(t, []).append(s)
    for ss in per_table.values():
        assert ss == sorted(ss)
    # the first 160 events are day 0: customers then orders (alphabetical here)
    first_day = [r[FIELD_TABLE] for r in all_rows(sink)[:DAY_ROWS]]
    assert first_day == ["customers"] * 40 + ["orders"] * 120


def test_the_drift_shows_in_the_stream():
    _, _, ev = plan_objects()
    rows = all_rows(run_to_memory(ev))
    orders = [r for r in rows if r[FIELD_TABLE] == "orders"]
    notes = [
        sum(r["note"] is None for r in orders[d * 120 : (d + 1) * 120]) / 120 for d in range(5)
    ]
    assert notes[0] < 0.15 and notes[4] > 0.4  # the null rate ramps up
    cancelled = [
        sum(r["status"] == "cancelled" for r in orders[d * 120 : (d + 1) * 120]) for d in range(5)
    ]
    assert cancelled[4] > cancelled[0] + 30  # the status mix moved from day 2 on
    tiers = {r["tier"] for r in rows if r[FIELD_TABLE] == "customers"}
    assert "platinum" in tiers


# ---- the answer key -------------------------------------------------------------------------


def test_the_answer_key_lists_every_active_event_of_every_day_with_its_first_seq(tmp_path):
    key_path = str(tmp_path / "key.jsonl")
    key = AnswerKey(key_path)
    _, _, ev = plan_objects(answer_key=key)
    run_to_memory(ev)
    key.close()
    drift = [r for r in read_answer_key(key_path) if r["kind"] == "drift"]
    by = {(r["event"], r["day"]): r for r in drift}
    # nulls: ramp over days 1-3 (1/3, 2/3, 1 and held), mix: from day 2, tiers: days 3 and 4 only
    assert {r["event"] for r in drift} == {"nulls", "mix", "tiers"}
    nulls = [by[("nulls", f"2026-03-0{d + 1}")] for d in range(1, 5)]
    assert [r["effect"] for r in nulls] == [0.333333, 0.666667, 1.0, 1.0]
    assert all(r["table"] == "orders" and r["column"] == "note" for r in nulls)
    assert [r["seq"] for r in nulls] == [120, 240, 360, 480]  # the first orders seq of the day
    assert [r["day_number"] for r in nulls] == [1, 2, 3, 4]
    assert [by[("mix", f"2026-03-0{d + 1}")]["effect"] for d in range(2, 5)] == [1.0] * 3
    tiers = [by[("tiers", "2026-03-04")], by[("tiers", "2026-03-05")]]
    assert [t["seq"] for t in tiers] == [120, 160]  # customers: 40 rows a day
    assert ("tiers", "2026-03-06") not in by and ("tiers", "2026-03-03") not in by
    assert all(r["key"] == f"{r['table']}/{r['seq']}" for r in drift)
    # nothing for day 0, where no event is active
    assert not [r for r in drift if r["day"] == "2026-03-01"]


def test_a_resumed_run_writes_the_drift_records_of_its_day_again_and_the_reader_collapses_them(
    tmp_path,
):
    path = str(tmp_path / "key.jsonl")
    key = AnswerKey(path)
    _, _, ev = plan_objects(answer_key=key)
    ck = tmp_path / "ck.json"
    run_to_memory(ev, checkpoint_path=str(ck), max_events=3 * DAY_ROWS)
    doc = json.loads(ck.read_text())
    doc.update(offset=2 * DAY_ROWS + 5, complete=False)
    ck.write_text(json.dumps(doc))
    key.close()
    again = AnswerKey(path, append=True)
    _, _, ev2 = plan_objects(answer_key=again)
    run_to_memory(ev2, checkpoint_path=str(ck))
    again.close()
    raw = [json.loads(x) for x in open(path)]
    drift = [r for r in raw if r["kind"] == "drift"]
    assert len(drift) > len({(r["event"], r["day"]) for r in drift})  # day 2 appears twice
    assert len([r for r in read_answer_key(path) if r["kind"] == "drift"]) == len(
        {(r["event"], r["day"]) for r in drift}
    )


# ---- identity and resume --------------------------------------------------------------------


def test_the_plan_digest_is_part_of_the_checkpoint_fingerprint():
    a = plan_objects(plan_sha256="a" * 64)[2]
    b = plan_objects(plan_sha256="b" * 64)[2]
    c = plan_objects(plan_sha256="a" * 64)[2]
    assert a.fingerprint() == c.fingerprint() != b.fingerprint()
    other = dict(PLAN_DOC, events=PLAN_DOC["events"][:2])
    d = plan_objects(plan_doc=other, plan_sha256="a" * 64)[2]
    assert d.fingerprint() != a.fingerprint()  # the events themselves count as well


def test_a_checkpoint_of_another_plan_is_refused(files, capsys, tmp_path):
    ck = tmp_path / "ck.json"
    argv = drift_args(files, "--checkpoint", str(ck), "--max-events", "200")
    assert main(argv) == 0
    capsys.readouterr()
    assert main(argv) == 0  # the same plan resumes
    other = tmp_path / "other.json"
    other.write_text(json.dumps(dict(PLAN_DOC, days=6)))
    argv2 = [*argv]
    argv2[argv2.index("--drift-plan") + 1] = str(other)
    assert main(argv2) == 2
    assert "belongs to a different stream" in capsys.readouterr().err
    assert main([*argv2, "--fresh"]) == 0  # --fresh starts over


def test_a_crashed_and_resumed_run_delivers_the_events_of_the_uninterrupted_run(files, tmp_path):
    """The package's acceptance: poisson arrivals, a daily curve and a drift plan together."""
    pacing = ["--realtime", "--rate", "200000", "--arrivals", "poisson",
              "--daily-curve", "business-hours", "--out-of-order", "0.1", "--ooo-window", "50",
              "--duplicate-fraction", "0.05", "--anomaly-fraction", "0.05"]  # fmt: skip
    whole = files["out"]
    assert main(drift_args(files, "--sink", "file", "-o", str(whole), *pacing)) == 0
    full = whole.read_bytes().splitlines()
    part = tmp_path / "part.jsonl"
    ck = tmp_path / "ck.json"
    argv = drift_args(files, "--sink", "file", "-o", str(part), "--checkpoint", str(ck), *pacing)
    assert main([*argv, "--max-events", str(2 * DAY_ROWS + 77)]) == 0
    doc = json.loads(ck.read_text())
    assert doc["offset"] == 2 * DAY_ROWS + 77
    doc.update(offset=DAY_ROWS + 31, complete=False)  # a crash: the checkpoint is behind
    ck.write_text(json.dumps(doc))
    assert main(argv) == 0
    resumed = part.read_bytes().splitlines()
    assert len(resumed) > len(full)  # the stale checkpoint and the duplicates add repeats

    def first_per_key(lines: list[bytes]) -> list[bytes]:
        seen, out = set(), []
        for line in lines:
            e = json.loads(line)
            k = (e[FIELD_TABLE], e[FIELD_SEQ])
            if k not in seen:
                seen.add(k)
                out.append(line)
        return out

    def by_key(lines: list[bytes]) -> dict[Any, bytes]:
        return {
            (json.loads(x)[FIELD_TABLE], json.loads(x)[FIELD_SEQ]): x for x in first_per_key(lines)
        }

    assert by_key(resumed) == by_key(full)


# ---- --day-seconds --------------------------------------------------------------------------


def test_the_day_schedule_gives_each_day_its_seconds_and_spreads_its_events():
    s = DaySchedule([100, 50, 200], 10.0)
    assert s.due_time(0) == 0.0
    assert s.due_time(50) == pytest.approx(5.0)  # halfway through day 0
    assert s.due_time(100) == pytest.approx(10.0)  # the first event of day 1
    assert s.due_time(125) == pytest.approx(15.0)
    assert s.due_time(150) == pytest.approx(20.0)  # day 2 starts at 2 x 10 s
    assert s.due_time(250) == pytest.approx(25.0)
    assert s.due_time(350) == pytest.approx(30.0)  # after the last event: the end of the plan
    assert s.due_time(10_000) == pytest.approx(30.0)
    assert s.expected_by(25.0) == pytest.approx(250)
    assert s.expected_by(12.0) == pytest.approx(100 + 10)
    assert [s.due_time(n) for n in range(0, 351, 7)] == sorted(
        s.due_time(n) for n in range(0, 351, 7)
    )


def test_the_day_schedule_after_a_resume_restarts_its_clock_at_the_offset():
    s = DaySchedule([100, 100], 10.0)
    s.resume_at(150)  # in the middle of day 1
    assert s.due_time(0) == 0.0
    assert s.due_time(25) == pytest.approx(2.5)
    assert s.due_time(50) == pytest.approx(5.0)  # the end of the plan


def test_the_day_schedule_checks_its_arguments():
    for bad in (0, -1, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="day_seconds"):
            DaySchedule([10], bad)
    with pytest.raises(ValueError, match="at least one day"):
        DaySchedule([], 1.0)
    # a day without events still takes its time
    s = DaySchedule([10, 0, 10], 4.0)
    assert s.due_time(10) == pytest.approx(8.0)  # day 2 starts after day 1 has had its 4 s


def test_the_runner_paces_the_days_by_day_seconds():
    _, _, ev = plan_objects()
    deadlines: list[float] = []

    def sleep_until(_stop: Any, deadline: float) -> None:
        deadlines.append(deadline)

    cfg = EmitConfig(realtime=True, day_seconds=2.0, batch_events=40, max_events=2 * DAY_ROWS)
    runner = EmitRunner(ev, MemorySink(), cfg, sleep_until=sleep_until)
    assert isinstance(runner.schedule, DaySchedule)
    runner.run()
    rel = [d - deadlines[0] for d in deadlines]
    # 8 batches of 40 events; 160 events a day in 2 s: batch k is due k x 40 / 80 seconds in
    assert rel == pytest.approx([k * 0.5 for k in range(8)])


# ---- the command line -----------------------------------------------------------------------


def test_drift_plan_to_a_file_and_the_report(files, capsys):
    assert main(drift_args(files, "--sink", "file", "-o", str(files["out"]), "--json")) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["events"] == 5 * DAY_ROWS and report["complete"] is True
    assert len(files["out"].read_bytes().splitlines()) == 5 * DAY_ROWS


def test_shape_stream_with_a_drift_plan_is_in_event_time_order_per_day(files, capsys):
    argv = [
        "stream", str(files["schema"]), "-t", "orders", "--drift-plan", str(files["plan"]),
        "--rows", "orders=120", "--rows", "customers=40", "--sink", "file", "-o", str(files["out"]),
    ]  # fmt: skip
    assert main(argv) == 0
    events = [json.loads(x) for x in files["out"].read_bytes().splitlines()]
    assert len(events) == 5 * 120
    assert [e[FIELD_SEQ] for e in events[:120]] != list(range(120))  # sorted by event time
    for d in range(5):
        day = events[d * 120 : (d + 1) * 120]
        assert sorted(e[FIELD_SEQ] for e in day) == list(range(d * 120, (d + 1) * 120))
        times = [e[FIELD_TIME] for e in day]
        assert times == sorted(times)


def test_the_flags_and_what_they_need(files, capsys):
    base = ["emit", str(files["schema"])]
    for argv, message in [
        ([*base, "--rows", "orders=5"], "--rows needs --drift-plan"),
        ([*base, "--day-seconds", "5"], "--day-seconds needs --drift-plan"),
        (drift_args(files, "--day-seconds", "5"), "--day-seconds needs --realtime"),
        (drift_args(files, "--realtime", "--day-seconds", "5", "--rate", "50"),
         "--day-seconds sets the pace"),
        (drift_args(files, "--realtime", "--day-seconds", "5", "--burst", "0:1:2"),
         "--day-seconds sets the pace"),
        (drift_args(files, "--realtime", "--day-seconds", "0"), "--day-seconds must be positive"),
        ([*base, "--drift-plan", str(files["plan"]), "--rows", "orders"], "TABLE=N"),
        ([*base, "--drift-plan", str(files["plan"]), "--rows", "nope=5"], "no table 'nope'"),
        ([*base, "--drift-plan", str(files["plan"]), "--rows", "orders=x"], "TABLE=N"),
        ([*base, "--drift-plan", str(files["schema"] .parent / "missing.json")], "missing.json"),
    ]:  # fmt: skip
        assert main(argv) == 2, argv
        assert message in capsys.readouterr().err, argv


def test_a_bad_plan_is_refused_before_anything_is_sent(files, capsys, tmp_path):
    bad = tmp_path / "bad.json"
    for doc, message in [
        ("{", "not valid JSON"),
        (json.dumps([1]), "must be a JSON object"),
        (json.dumps({"events": [{"kind": "x", "table": "orders", "column": "a", "start": 0}]}),
         "unknown event kind"),
        (json.dumps({"days": 3, "events": [{"kind": "null_rate", "table": "orders",
                                            "column": "nope", "to": 0.5, "start": 0}]}),
         "nope"),
    ]:  # fmt: skip
        bad.write_text(doc)
        assert main(drift_args({**files, "plan": bad})) == 2, doc
        assert message in capsys.readouterr().err, doc
    assert not files["out"].exists()


def test_the_plan_digest_is_the_sha256_of_the_plan_file(files):
    from shape.cli.emit import plan_digest

    assert plan_digest(str(files["plan"])) == hashlib.sha256(files["plan"].read_bytes()).hexdigest()


def test_without_a_drift_plan_nothing_changes(capsys):
    argv = ["emit", "retail", "--table", "customer", "--max-events", "5"]
    assert main(argv) == 0
    assert capsys.readouterr().out.count("\n") == 5


def test_days_without_a_date_column_still_stream(files, capsys):
    argv = [
        "emit", str(files["schema"]), "--table", "customers", "--drift-plan", str(files["plan"]),
        "--rows", "customers=40", "--json",
    ]  # fmt: skip
    assert main(argv) == 0
    # the events go to standard output (the console sink), the report to standard error
    assert json.loads(capsys.readouterr().err.strip().splitlines()[-1])["events"] == 5 * 40


def test_the_first_days_of_a_plan_can_be_streamed_with_max_events(files, capsys):
    argv = drift_args(files, "--max-events", str(DAY_ROWS + 10), "--json")
    assert main(argv) == 0
    out = capsys.readouterr().err.strip().splitlines()
    assert json.loads(out[-1])["events"] == DAY_ROWS + 10
    assert dt.date.fromisoformat(PLAN_DOC["start"]).isoformat() == "2026-03-01"


def test_a_column_the_plan_adds_or_drops_appears_and_disappears_from_its_day():
    doc = {
        "start": "2026-03-01",
        "days": 4,
        "events": [
            {"id": "plus", "kind": "add_column", "table": "orders", "column": "channel",
             "definition": {"type": "string",
                            "generator": {"strategy": "weighted_enum",
                                          "values": {"web": 1, "store": 1}}},
             "start": 1, "end": 3},
            {"id": "minus", "kind": "drop_column", "table": "orders", "column": "note",
             "start": 2},
        ],
    }  # fmt: skip
    schema, plan, ev = plan_objects(plan_doc=doc)
    rows = all_rows(run_to_memory(ev))
    orders = [r for r in rows if r[FIELD_TABLE] == "orders"]
    for d in range(4):
        day = orders[d * 120 : (d + 1) * 120]
        names = set(day[0])
        assert ("channel" in names) == (d in (1, 2)), d  # added on day 1, gone again on day 3
        assert ("note" in names) == (d < 2), d  # dropped from day 2 on
        expect = plan.generate_day(schema, d, row_counts=ROWS).tables["orders"]
        assert set(expect.column_names) | {FIELD_TABLE, FIELD_SEQ, FIELD_TIME} == names
    assert [r[FIELD_SEQ] for r in orders] == list(range(480))  # the key is unaffected
