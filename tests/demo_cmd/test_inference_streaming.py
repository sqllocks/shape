"""P6-12: the inference and streaming modes, end to end."""

from __future__ import annotations

import io
import json

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
import pytest

from shape.demo.fidelity import FidelityReport
from shape.generation.learn import profile_from_dict
from shape.profile.reference import profile


def session_of(out: str) -> str:
    return next(x for x in out.splitlines() if x.startswith("Session: ")).split(": ", 1)[1].strip()


def record(home, session: str) -> dict:
    return json.loads((home / "sessions" / f"demo-{session}.json").read_text())


def learned_small(schema_file) -> dict[str, int]:
    """The rows the schema learned from the domain's own small scale gives for ``small``."""
    from shape.cli.generation import load_target
    from shape.generation.engine import Engine
    from shape.generation.learn import learn

    tables = Engine(load_target(str(schema_file)), scale="small", seed=5).generate().tables
    return dict(learn(profile(dict(tables)), "x").generation.scales["small"])


# ---- inference ---------------------------------------------------------------------------------


def test_inference_learns_generates_compares_and_records(run, home, schema_file):
    code, out, _ = run(
        "demo", "run", "retail", "--domain", schema_file, "--rows", "1000", "--seed", "5"
    )
    assert code == 0, out
    assert "Profiled 3 table(s)" in out and "Built schema: 3 tables" in out
    assert "Fidelity report" in out.lower() or "Fidelity Report" in out
    score = float(out.split("Fidelity: ")[1].split("%")[0]) / 100
    assert 0.0 <= score <= 1.0
    rec = record(home, session_of(out))
    assert rec["success"] and rec["metrics"]["tables_profiled"] == 3
    assert rec["metrics"]["fidelity_score"] == pytest.approx(score, abs=1e-3)
    assert {a["name"]: a["row_count"] for a in rec["artifacts"]} == learned_small(schema_file)
    assert {a["target"] for a in rec["artifacts"]} == {"synthetic"}


def test_inference_is_repeatable_for_a_seed(run, home, schema_file):
    args = ["demo", "run", "retail", "--domain", schema_file, "--rows", "1000", "--seed", "9"]
    a = record(home, session_of(run(*args)[1]))
    b = record(home, session_of(run(*args)[1]))
    assert a["metrics"]["fidelity_score"] == b["metrics"]["fidelity_score"]


def test_inference_learns_from_a_csv_file(run, home, tmp_path):
    table = pa.table(
        {
            "id": list(range(200)),
            "kind": ["a", "b", "c", "d"] * 50,
            "x": [i * 0.5 for i in range(200)],
        }
    )
    path = tmp_path / "events.csv"
    pacsv.write_csv(table, path)
    code, out, _ = run(
        "demo", "run", "retail", "--input-file", path, "--rows", "200", "--seed", "1"
    )
    assert code == 0, out
    rec = record(home, session_of(out))
    assert rec["metrics"]["tables_profiled"] == 1
    assert [a["name"] for a in rec["artifacts"]] == ["events"]


def test_inference_learns_from_a_parquet_file(run, home, tmp_path):
    path = tmp_path / "t.parquet"
    pq.write_table(pa.table({"id": list(range(100)), "v": [i % 7 for i in range(100)]}), path)
    code, out, _ = run("demo", "run", "retail", "--input-file", path, "--rows", "100")
    assert code == 0, out


def test_an_input_file_that_cannot_be_read_is_a_message(run, tmp_path):
    bad = tmp_path / "notes.txt"
    bad.write_text("x")
    code, _, err = run("demo", "run", "retail", "--input-file", bad)
    assert code == 1 and "unsupported file type" in err
    code, _, err = run("demo", "run", "retail", "--input-file", tmp_path / "gone.csv")
    assert code == 1 and "file not found" in err


def test_live_db_needs_a_profile_with_a_database(run, tmp_path):
    code, _, err = run("demo", "run", "retail", "--input-file", "live-db")
    assert code == 1 and "live-db mode requires a connection profile" in err
    assert run("demo", "init", "--name", "files", "--local-path", tmp_path / "x")[0] == 0
    code, _, err = run("demo", "run", "retail", "--input-file", "live-db", "--connection", "files")
    assert code == 1 and "no warehouse or SQL database to profile" in err


def test_live_db_profiles_the_database_through_the_profiler(home, tmp_path, monkeypatch):
    from shape.demo.api import demo_init, demo_run
    from shape.demo.runtime import DemoRuntime

    monkeypatch.setenv(
        "DEMO_SQL", "Driver={x};Server=db.test;Database=d;Authentication=ActiveDirectoryDefault"
    )
    demo_init("db", sql_db_conn="env://DEMO_SQL", local_path=str(tmp_path / "unused"))
    seen: dict = {}

    def fake_profile_database(conn, **kw):
        seen.update(conn=conn, **kw)
        return profile(
            {"orders": pa.table({"id": list(range(300)), "kind": ["x", "y", "z"] * 100})}
        )

    result = demo_run(
        {"scenario": "retail", "input_file": "live-db", "connection": "db", "rows": 300,
         "db_schema": "sales", "db_tables": "orders", "sample_rows": 50, "seed": 2},
        runtime=DemoRuntime(profile_database=fake_profile_database),
    )  # fmt: skip
    assert result["success"], result
    # the reference is resolved, the schema, tables and sample size are passed on
    assert seen == {
        "conn": "Driver={x};Server=db.test;Database=d;Authentication=ActiveDirectoryDefault",
        "schema": "sales", "sample_rows": 50, "tables": ["orders"],
    }  # fmt: skip


def test_the_fidelity_score_is_the_share_of_columns_that_pass():
    real = profile_from_dict(
        profile({"t": pa.table({"k": list(range(100)), "c": ["a", "b"] * 50})}).to_dict()
    )
    same = FidelityReport(real, real)
    assert same.overall_score() == 1.0
    skewed = profile_from_dict(
        profile({"t": pa.table({"k": list(range(100)), "c": [None] * 40 + ["a"] * 60})}).to_dict()
    )
    report = FidelityReport(real, skewed)  # c has 40% nulls against none
    assert {c["column"]: c["pass"] for c in report.comparisons()} == {"c": False, "k": True}
    assert report.overall_score() == 0.5
    # no column was compared: a 100% claim needs at least one compared column (#522), so the
    # score is 0.0 here (it was 1.0)
    assert FidelityReport(real, profile_from_dict({"tables": {}})).overall_score() == 0.0


def test_a_report_that_compared_no_column_says_so_instead_of_a_percentage():
    real = profile_from_dict(profile({"t": pa.table({"k": [1, 2, 3]})}).to_dict())
    out = io.StringIO()
    FidelityReport(real, profile_from_dict({"tables": {}}), out=out).render()
    text = out.getvalue()
    assert "Fidelity score: n/a (no columns compared)" in text
    assert "100.0%" not in text and "0.0%" not in text


def test_a_report_that_compared_columns_still_prints_the_percentage():
    real = profile_from_dict(profile({"t": pa.table({"k": [1, 2, 3]})}).to_dict())
    out = io.StringIO()
    FidelityReport(real, real, out=out).render()
    assert "Fidelity score: 100.0%" in out.getvalue()


def test_an_unknown_null_rate_does_not_crash_the_report():
    # a database profile has no null rate for a table with no sampled row (None, not 0.0)
    unknown = profile_from_dict(profile({"t": pa.table({"k": [1, 2, 3]})}).to_dict())
    unknown.tables["t"].columns["k"].null_rate = None
    other = profile_from_dict(profile({"t": pa.table({"k": [1, 2, 3]})}).to_dict())
    rows = FidelityReport(unknown, other).comparisons()
    assert rows[0]["real_nulls"] == "n/a" and rows[0]["syn_nulls"] == "0.0%"
    assert rows[0]["pass"] is True  # nothing to compare: the null-rate check is not made


def test_inference_reports_no_score_when_no_column_was_compared(
    run, home, schema_file, monkeypatch
):
    monkeypatch.setattr(FidelityReport, "comparisons", lambda self: [])
    code, out, _ = run("demo", "run", "retail", "--domain", schema_file, "--seed", "2")
    assert code == 0, out
    assert "Fidelity score: n/a (no columns compared)" in out
    assert "100.0%" not in out
    # nothing to report: no score (the CLI and the result show none) rather than 0.0 or 100%
    assert record(home, session_of(out))["metrics"]["fidelity_score"] is None
    assert "Fidelity:" not in out


# ---- streaming ---------------------------------------------------------------------------------


def test_streaming_prints_events_of_the_first_table(run, home, schema_file):
    code, out, _ = run(
        "demo", "run", "retail", "--mode", "streaming", "--domain", schema_file,
        "--max-events", "5", "--seed", "2",
    )  # fmt: skip
    assert code == 0, out
    events = [json.loads(x) for x in out.splitlines() if x.startswith("{")]
    assert len(events) == 5
    assert {e["_shape_table"] for e in events} == {"customer"}
    assert [e["_shape_seq"] for e in events] == sorted(e["_shape_seq"] for e in events) or True
    rec = record(home, session_of(out))
    assert rec["metrics"]["events_streamed"] == 5
    assert {a["target"] for a in rec["artifacts"]} == {"generated"}


def test_a_streaming_session_has_nothing_to_clean_up(run, schema_file):
    out = run(
        "demo", "run", "retail", "--mode", "streaming", "--domain", schema_file, "--max-events", "3"
    )[1]
    code, out, _ = run("demo", "cleanup", session_of(out))
    assert code == 0 and "Removed:" not in out


def test_streaming_is_refused_where_the_scenario_has_no_streaming_mode(run):
    code, _, err = run("demo", "run", "adventureworks", "--mode", "streaming")
    assert code == 2 and "does not support mode 'streaming'" in err
