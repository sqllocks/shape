"""P6-12: ``shape demo run`` end to end with the local sinks, then ``status``, ``report`` and
``cleanup`` on what it made."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from demo_helpers import ROWS, write_schema

from shape.demo.cleanup import MARKER


def parts_rows(folder: Path) -> int:
    return sum(pq.ParquetFile(p).metadata.num_rows for p in sorted(folder.glob("part-*.parquet")))


def session_of(out: str) -> str:
    line = next(x for x in out.splitlines() if x.startswith("Session: "))
    return line.split(": ", 1)[1].strip()


def manifest(home, session: str) -> dict:
    return json.loads((home / "sessions" / f"demo-{session}.json").read_text())


@pytest.fixture
def local_profile(run, tmp_path):
    target = tmp_path / "landing"
    assert run("demo", "init", "--name", "local", "--local-path", target)[0] == 0
    return target


# ---- seeding to a folder -----------------------------------------------------------------------


def test_seeding_writes_every_table_with_its_own_row_count(run, home, local_profile, schema_file):
    code, out, _ = run(
        "demo", "run", "retail", "--mode", "seeding", "--connection", "local",
        "--domain", schema_file, "--rows", "1000", "--seed", "3",
    )  # fmt: skip
    assert code == 0, out
    session = session_of(out)
    folder = local_profile / session
    for table, rows in ROWS.items():
        assert parts_rows(folder / table) == rows
    record = manifest(home, session)
    assert record["success"] is True and record["scale_mode"] == "local"
    # one artifact per table, with that table's rows (the rows of every table are not a total)
    assert {a["name"]: a["row_count"] for a in record["artifacts"]} == ROWS
    assert {a["target"] for a in record["artifacts"]} == {"file"}
    assert record["metrics"]["rows_generated"] == sum(ROWS.values())


def test_seeding_is_repeatable_for_a_seed(run, local_profile, schema_file):
    args = ["demo", "run", "retail", "--mode", "seeding", "--connection", "local"]
    args += ["--domain", schema_file, "--rows", "1000", "--seed", "11"]
    first = session_of(run(*args)[1])
    second = session_of(run(*args)[1])
    for table in ROWS:
        a = pq.read_table(sorted((local_profile / first / table).glob("*.parquet"))[0])
        b = pq.read_table(sorted((local_profile / second / table).glob("*.parquet"))[0])
        assert a.equals(b)


def test_seeding_with_no_profile_generates_and_counts_but_writes_nothing(
    run, home, tmp_path, schema_file, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    code, out, _ = run(
        "demo", "run", "retail", "--mode", "seeding", "--domain", schema_file, "--rows", "1000"
    )
    assert code == 0
    record = manifest(home, session_of(out))
    assert {a["name"]: a["row_count"] for a in record["artifacts"]} == ROWS
    assert {a["target"] for a in record["artifacts"]} == {"generated"}
    assert sorted(p.name for p in tmp_path.iterdir() if p.name != "home") == ["shop.json"]


def test_a_composite_run_writes_each_domain_to_its_own_folder(run, home, local_profile, tmp_path):
    a = write_schema(tmp_path / "alpha.schema.json", {"customer": 5, "order": 9, "order_line": 12})
    b = write_schema(tmp_path / "beta.json", {"customer": 7, "order": 8, "order_line": 6})
    code, out, _ = run(
        "demo", "run", "enterprise", "--mode", "seeding", "--connection", "local",
        "--domains", f"{a},{b}", "--rows", "1000",
    )  # fmt: skip
    assert code == 0, out
    folder = local_profile / session_of(out)
    assert parts_rows(folder / "alpha" / "customer") == 5
    assert parts_rows(folder / "beta" / "order_line") == 6
    assert parts_rows(folder / "beta" / "customer") == 7


def test_a_scenario_runs_its_own_domain_not_retail(
    run, home, local_profile, missing_domain_scenario
):
    # the scenario's own domain is not installed: the run must say so, not run retail instead
    code, out, err = run(
        "demo", "run", missing_domain_scenario, "--mode", "seeding", "--connection", "local",
        "--rows", "1000",
    )  # fmt: skip
    assert code == 1
    assert "no domain named 'no-such-domain'" in err
    record = manifest(home, next(p.stem[5:] for p in (home / "sessions").glob("demo-*.json")))
    assert record["success"] is False and record["artifacts"] == []
    assert not any(local_profile.glob("*/customer")) if local_profile.exists() else True


def test_a_scale_the_domain_lacks_is_a_message(run, schema_file):
    code, _, err = run(
        "demo", "run", "retail", "--mode", "seeding", "--domain", schema_file, "--rows", "9000"
    )
    assert code == 1 and "has no 'medium' scale" in err


def test_dry_run_and_estimate_generate_nothing_in_every_mode(run, home, local_profile, schema_file):
    for mode in ("inference", "seeding", "streaming"):
        for flag in ("--dry-run", "--estimate"):
            code, out, _ = run(
                "demo", "run", "retail", "--mode", mode, "--connection", "local",
                "--domain", schema_file, flag,
            )  # fmt: skip
            assert code == 0 and "Cost estimate" in out
            assert ("[dry-run]" in out) == (flag == "--dry-run")
    assert not local_profile.exists() or not any(local_profile.iterdir())
    for path in (home / "sessions").glob("demo-*.json"):
        assert json.loads(path.read_text())["artifacts"] == []


# ---- cleanup -----------------------------------------------------------------------------------


def test_cleanup_removes_the_session_folder_and_says_what_it_removed(
    run, home, local_profile, schema_file
):
    out = run(
        "demo", "run", "retail", "--mode", "seeding", "--connection", "local",
        "--domain", schema_file, "--rows", "1000",
    )[1]  # fmt: skip
    session = session_of(out)
    code, out, _ = run("demo", "cleanup", session, "--dry-run")
    assert code == 0 and "[dry-run] Would remove: file/customer" in out
    assert (local_profile / session / "customer").exists()  # a dry run removes nothing

    code, out, _ = run("demo", "cleanup", session)
    assert code == 0
    for table in ROWS:
        assert f"Removed: file/{table}" in out
    assert not (local_profile / session).exists()  # the session folder itself is gone
    assert local_profile.exists()  # the user's own folder is not

    code, out, _ = run("demo", "cleanup", session)  # a second cleanup finds nothing left
    assert code == 0 and "Removed:" not in out and "already gone" in out


def test_cleanup_never_removes_what_the_session_did_not_create(run, home, local_profile, tmp_path):
    session = "abc12345"
    victim = tmp_path / "precious"
    victim.mkdir()
    (victim / "data.txt").write_text("keep me")
    (home / "sessions").mkdir(parents=True)
    record = {
        "session_id": session, "scenario": "retail", "mode": "seeding", "started_at": "t",
        "finished_at": "t", "success": True, "error": None, "params": {}, "metrics": {},
        "artifacts": [
            {"target": "file", "name": "precious", "row_count": 0, "detail": str(victim)},
            {"target": "file", "name": "x", "row_count": 0, "detail": str(victim / "data.txt")},
            {"target": "file", "name": "root", "row_count": 0, "detail": "/"},
        ],
    }  # fmt: skip
    (home / "sessions" / f"demo-{session}.json").write_text(json.dumps(record))
    code, out, _ = run("demo", "cleanup", session)
    assert code == 0 and "Removed:" not in out
    assert out.count("not inside a folder this session created") == 3
    assert (victim / "data.txt").read_text() == "keep me"


def test_a_marker_of_another_session_does_not_make_a_folder_removable(
    run, home, local_profile, tmp_path
):
    other = tmp_path / "other"
    other.mkdir()
    (other / MARKER).write_text("someone-else\n")
    (other / "t").mkdir()
    (home / "sessions").mkdir(parents=True)
    record = {
        "session_id": "mine0001", "scenario": "retail", "mode": "seeding", "started_at": "t",
        "params": {}, "metrics": {}, "success": True, "finished_at": None, "error": None,
        "artifacts": [{"target": "file", "name": "t", "row_count": 1, "detail": str(other / "t")}],
    }  # fmt: skip
    (home / "sessions" / "demo-mine0001.json").write_text(json.dumps(record))
    code, out, _ = run("demo", "cleanup", "mine0001")
    assert code == 0 and (other / "t").exists()


def test_cleanup_of_an_inference_session_removes_nothing_and_says_so(run, schema_file):
    out = run("demo", "run", "retail", "--domain", schema_file, "--rows", "1000")[1]
    code, out, _ = run("demo", "cleanup", session_of(out))
    assert code == 0 and "held in memory only" in out and "Removed:" not in out


@pytest.mark.parametrize("session", ["../../etc/passwd", "a/b", "..", "x" * 90])
def test_a_session_id_is_a_plain_name_so_no_file_outside_sessions_is_read(
    run, home, tmp_path, session
):
    (home / "sessions").mkdir(parents=True)
    (tmp_path / "outside.json").write_text("{}")
    for command in ("status", "report", "cleanup"):
        code, _, err = run("demo", command, session)
        assert code == 2 and "not a plain name" in err


def test_an_unknown_session_is_exit_two(run):
    for command in ("status", "report", "cleanup"):
        code, _, err = run("demo", command, "nothere1")
        assert code == 2 and "no session 'nothere1'" in err


# ---- status and report -------------------------------------------------------------------------


def test_status_and_report_describe_the_session(run, home, local_profile, schema_file, tmp_path):
    out = run(
        "demo", "run", "retail", "--mode", "seeding", "--connection", "local",
        "--domain", schema_file, "--rows", "1000",
    )[1]  # fmt: skip
    session = session_of(out)
    code, out, _ = run("demo", "status", session)
    assert code == 0
    assert f"Session: {session}" in out and "Scenario: retail (seeding)" in out
    assert "Status: Success" in out and "Artifacts: 3" in out

    code, out, _ = run("demo", "status", session, "--json")
    assert json.loads(out)["manifest"]["session_id"] == session

    code, out, _ = run("demo", "report", session)
    assert "| Total rows | 4,340 |" in out and "| file | order | 1,200 |" in out

    target = tmp_path / "r" / "report.html"
    code, out, _ = run("demo", "report", session, "--format", "html", "--output", target)
    assert code == 0 and "Report written to" in out
    text = target.read_text()
    assert text.startswith("<!DOCTYPE html>") and "<td>order</td><td>1,200</td>" in text


def test_a_report_escapes_what_the_record_holds(run, home):
    (home / "sessions").mkdir(parents=True)
    record = {
        "session_id": "evil0001", "scenario": "<script>alert(1)</script>", "mode": "a|b\nc",
        "started_at": "t", "finished_at": None, "success": False, "error": "<b>x</b>",
        "params": {}, "metrics": {},
        "artifacts": [{"target": "t|x", "name": "<img src=x>", "row_count": 1, "detail": ""}],
    }  # fmt: skip
    (home / "sessions" / "demo-evil0001.json").write_text(json.dumps(record))
    html = run("demo", "report", "evil0001", "--format", "html")[1]
    assert "<script>" not in html and "&lt;script&gt;" in html and "<img src=x>" not in html
    md = run("demo", "report", "evil0001")[1]
    assert "| Mode | a\\|b c |" in md and "| t\\|x | <img src=x> | 1 |" in md


# ---- failure and rollback ----------------------------------------------------------------------


def test_a_failed_run_is_rolled_back_and_says_so(run, home, local_profile, tmp_path, monkeypatch):
    from shape.scale import router

    real = router.ScaleRouter.run

    def failing(self):  # the run writes the first table(s) and then dies
        sink = self.sinks[0]
        schema = self.engine.schema
        sink.open(schema)
        table = self.engine.order[0]
        for batch in self.engine.iter_chunks(table):
            sink.write_batch(table, batch)
        sink.finish_table(table)
        raise RuntimeError("the disk filled up")

    monkeypatch.setattr(router.ScaleRouter, "run", failing)
    schema = write_schema(tmp_path / "shop.json")
    code, out, err = run(
        "demo", "run", "retail", "--mode", "seeding", "--connection", "local",
        "--domain", schema, "--rows", "1000",
    )  # fmt: skip
    monkeypatch.setattr(router.ScaleRouter, "run", real)
    assert code == 1
    assert "the disk filled up" in err and "rolled back" in err
    assert not any(local_profile.iterdir())  # nothing of the failed run is left
    record = next(json.loads(p.read_text()) for p in (home / "sessions").glob("demo-*.json"))
    assert record["success"] is False and record["metrics"]["rolled_back"] >= 1


def test_run_json_prints_the_result_alone_on_standard_output(run, home, schema_file):
    code, out, err = run(
        "demo", "run", "retail", "--mode", "seeding", "--domain", schema_file, "--rows", "1000",
        "--json",
    )  # fmt: skip
    assert code == 0
    result = json.loads(out)  # nothing else is on standard output
    assert result["success"] is True and result["artifact_count"] == 3
    assert "Shape Demo — retail (seeding)" in err  # the progress went to standard error


def test_cleanup_of_a_session_whose_profile_was_deleted_still_removes_the_local_files(
    run, home, local_profile, schema_file
):
    out = run(
        "demo", "run", "retail", "--mode", "seeding", "--connection", "local",
        "--domain", schema_file, "--rows", "1000",
    )[1]  # fmt: skip
    session = session_of(out)
    (home / "connections.json").write_text("{}")  # the profile is gone
    code, out, _ = run("demo", "cleanup", session)
    assert code == 0 and "Removed: file/customer" in out
    assert not (local_profile / session).exists()
    code, _, err = run("demo", "cleanup", session, "--connection", "local")
    assert code == 2 and "no connection profile 'local'" in err  # asked for by name: an error
