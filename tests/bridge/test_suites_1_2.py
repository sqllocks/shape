"""W7-05 item 6 (second half): the bridge commands ``suite_list`` and ``suite_run``."""

from __future__ import annotations

import json
import threading
import time

import pytest

from shape.cli.main import main


def cli_json(capsys, *argv):
    code = main(list(argv))
    return code, _result(json.loads(capsys.readouterr().out))


def _result(doc):
    """The command's own result: W1-14 prints it inside the shape-result envelope (under
    ``payload`` when it is not an object or its keys clash with the envelope's)."""
    if isinstance(doc, dict) and doc.get("format") == "shape-result":
        if "payload" in doc:
            return doc["payload"]
        return {
            k: v for k, v in doc.items() if k not in ("format", "version", "command", "exit_code")
        }
    return doc


def wait(api, job_id, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = api.ok("job_status", job_id=job_id)
        if status["status"] not in ("running", "submitted"):
            return status
        time.sleep(0.05)
    raise AssertionError("the job did not end")


def write_suite(path, scenarios, **extra):
    path.write_text(
        json.dumps({"format": "shape-suite", "version": 1, "scenarios": scenarios, **extra})
    )
    return path


def normal(result):
    """The result without what differs between two runs (the elapsed time) and without `passed`,
    which only the bridge adds."""
    doc = json.loads(json.dumps(result))
    doc.pop("passed", None)
    for s in doc["scenarios"]:
        s["outcome"].pop("elapsed_seconds")
        s["outcome"].pop("files", None)
    return doc


@pytest.fixture(autouse=True)
def _domains(request):
    """Running a scenario needs the domains package (as the library tests do); listing does not."""
    if "suite_list" not in request.node.name:
        pytest.importorskip("shape_domains")


@pytest.fixture
def suite(tmp_path):
    return write_suite(tmp_path / "pair.json", ["clean_baseline", "nulls_injected"])


# ---- suite_list ------------------------------------------------------------------------------


def test_suite_list_is_what_the_cli_prints(api12, capsys):
    result = api12.ok("suite_list")
    code, cli = cli_json(capsys, "pack", "list", "--library", "--json")
    assert code == 0 and result == cli
    assert {"smoke", "schema-evolution"} <= set(result["suites"])
    assert result["suites"] == sorted(result["suites"])
    ids = {s["id"] for s in result["scenarios"]}
    assert {"clean_baseline", "nulls_injected", "duplicate_rows"} <= ids
    assert all({"id", "domain", "description"} <= set(s) for s in result["scenarios"])


def test_suite_list_takes_no_arguments_and_writes_nothing(api12, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    api12.fail("suite_list", "usage.unknown_argument", suite="smoke")
    before = sorted(p.name for p in tmp_path.rglob("*") if "jobs" not in p.parts)
    api12.ok("suite_list")
    assert sorted(p.name for p in tmp_path.rglob("*") if "jobs" not in p.parts) == before


# ---- suite_run: the result is the one `shape suite run --json` prints -------------------------


def test_suite_run_is_what_the_cli_prints_plus_passed(api12, suite, capsys):
    result = api12.ok("suite_run", suite=str(suite), scale="tiny", seed=5)
    code, cli = cli_json(
        capsys, "suite", "run", str(suite), "--scale", "tiny", "--seed", "5", "--json"
    )
    assert code == 0 and normal(result) == normal(cli)
    assert result["passed"] is True and result["met"] is True
    assert [s["scenario"] for s in result["scenarios"]] == ["clean_baseline", "nulls_injected"]
    for s in result["scenarios"]:
        assert s["met"] is True and s["mismatches"] == []
        assert (
            s["outcome"]["gates"] and s["outcome"]["scale"] == "tiny" and s["outcome"]["seed"] == 5
        )


def test_a_built_in_suite_is_run_by_name(api12):
    result = api12.ok("suite_run", suite="schema-evolution", scale="tiny")
    assert result["suite"] == "schema-evolution" and result["passed"] is True
    assert len(result["scenarios"]) >= 2


def test_a_scenario_that_misses_its_answer_key_is_reported_not_an_error(
    api12, suite, tmp_path, monkeypatch, capsys
):
    from shape.scenario.library import run as library_run

    real = library_run.mismatches

    def broken(expect, outcome):
        found = real(expect, outcome)
        if outcome.scenario == "nulls_injected":
            found = [library_run.Mismatch("gate not_null to fail", "gate not_null passed")]
        return found

    monkeypatch.setattr(library_run, "mismatches", broken)
    result = api12.ok("suite_run", suite=str(suite), scale="tiny")
    assert result["passed"] is False and result["met"] is False
    by = {s["scenario"]: s for s in result["scenarios"]}
    assert by["clean_baseline"]["met"] is True
    assert by["nulls_injected"]["met"] is False
    assert by["nulls_injected"]["mismatches"] == [
        {"expected": "gate not_null to fail", "observed": "gate not_null passed"}
    ]
    code, cli = cli_json(capsys, "suite", "run", str(suite), "--scale", "tiny", "--json")
    assert code == 1 and normal(cli) == normal(result)  # the CLI's exit 1 is `passed: false`


def test_output_dir_writes_each_scenario_under_its_name(api12, suite, tmp_path):
    out = tmp_path / "out"
    result = api12.ok("suite_run", suite=str(suite), scale="tiny", output_dir=str(out))
    assert sorted(p.name for p in out.iterdir()) == ["clean_baseline", "nulls_injected"]
    for s in result["scenarios"]:
        assert s["outcome"]["files"]
        assert all(f.startswith(str(out / s["scenario"])) for f in s["outcome"]["files"])


def test_no_output_dir_writes_nothing(api12, suite, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    before = sorted(p.name for p in tmp_path.rglob("*"))
    api12.ok("suite_run", suite=str(suite), scale="tiny")
    assert sorted(p.name for p in tmp_path.rglob("*") if "jobs" not in p.parts) == [
        n for n in before if "jobs" not in n
    ]


def test_the_same_seed_gives_the_same_result(api12, suite):
    one = api12.ok("suite_run", suite=str(suite), scale="tiny", seed=9)
    two = api12.ok("suite_run", suite=str(suite), scale="tiny", seed=9)
    assert normal(one) == normal(two)


# ---- errors and boundaries -------------------------------------------------------------------


def test_suite_run_errors(api12, suite, tmp_path):
    api12.fail("suite_run", "usage.missing_argument")
    api12.fail("suite_run", "usage.invalid_argument", suite=5)
    api12.fail("suite_run", "usage.invalid_argument", suite=str(suite), seed="x")
    api12.fail("suite_run", "usage.invalid_argument", suite=str(suite), scale=3)
    api12.fail("suite_run", "usage.unknown_argument", suite=str(suite), json=True)
    api12.fail("suite_run", "input.not_found", suite=str(tmp_path / "none.json"))
    api12.fail("suite_run", "input.not_found", suite="no-such-suite")
    api12.fail("suite_run", "input.invalid_value", suite=str(suite), scale="gigantic")


def test_a_malformed_suite_and_an_unknown_scenario_run_nothing(api12, tmp_path):
    out = tmp_path / "out"
    typo = write_suite(tmp_path / "typo.json", ["clean_baseline", "no_such_scenario"])
    error = api12.fail("suite_run", "input.invalid_value", suite=str(typo), output_dir=str(out))
    assert "no_such_scenario" in error["message"]
    assert not out.exists()  # the first scenario was not run either
    empty = write_suite(tmp_path / "empty.json", [])
    api12.fail("suite_run", "input.invalid_value", suite=str(empty))
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"format": "something-else", "version": 1, "scenarios": ["x"]}))
    api12.fail("suite_run", "input.invalid_value", suite=str(wrong))
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    api12.fail("suite_run", "input.invalid_value", suite=str(broken))  # not valid JSON


def test_a_suite_of_a_newer_version_is_unsupported(api12, tmp_path):
    newer = tmp_path / "newer.json"
    newer.write_text(json.dumps({"format": "shape-suite", "version": 99, "scenarios": ["x"]}))
    api12.fail("suite_run", "input.unsupported_format_version", suite=str(newer))


def test_a_destination_that_is_not_local_is_not_permitted(api12, suite, tmp_path):
    for target in ("s3://bucket/out", "abfss://c@acct.dfs.core.windows.net/x", "https://h/x"):
        api12.fail("suite_run", "policy.not_permitted", suite=str(suite), output_dir=target)
    api12.fail(
        "suite_run",
        "policy.not_permitted",
        {"async": True},
        suite=str(suite),
        output_dir="s3://b/o",
    )
    assert [j["status"] for j in api12.ok("job_list")["jobs"]] == []  # a refused job is not stored


def test_an_unwritable_output_dir_is_io_write_failed(api12, suite, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a folder")
    api12.fail("suite_run", "io.write_failed", suite=str(suite), scale="tiny",
               output_dir=str(blocker / "sub"))  # fmt: skip


# ---- job, cancel -----------------------------------------------------------------------------


def test_suite_run_runs_as_a_job(api12, suite):
    started = api12.ok("suite_run", {"async": True}, suite=str(suite), scale="tiny", seed=2)
    assert started["cancellable"] is True
    done = wait(api12, started["job_id"])
    assert done["status"] == "succeeded"
    assert normal(done["result"]) == normal(
        api12.ok("suite_run", suite=str(suite), scale="tiny", seed=2)
    )


def test_a_cancelled_suite_run_job_is_cancelled_with_its_partial_counts(
    api12, tmp_path, monkeypatch
):
    from shape.bridge.handlers import suites

    three = write_suite(
        tmp_path / "three.json", ["clean_baseline", "nulls_injected", "duplicate_rows"]
    )
    real = suites.run_scenario
    calls: list[str] = []
    second = threading.Event()

    def slow(name, **kwargs):
        calls.append(name)
        if len(calls) == 2:
            second.set()
            time.sleep(1.5)  # the cancel request arrives while the second scenario runs
        return real(name, **kwargs)

    monkeypatch.setattr(suites, "run_scenario", slow)
    started = api12.ok("suite_run", {"async": True}, suite=str(three), scale="tiny")
    assert second.wait(60)
    api12.ok("job_cancel", job_id=started["job_id"])
    status = wait(api12, started["job_id"])
    assert status["status"] == "cancelled", status
    assert status["result"] == {
        "suite": "three",
        "scenarios_run": 2,
        "scenarios_total": 3,
        "met": 2,
        "not_met": 0,
    }
    assert calls == ["clean_baseline", "nulls_injected"]  # the third never started


def test_a_suite_that_is_not_cancelled_runs_every_scenario(api12, tmp_path):
    three = write_suite(
        tmp_path / "three.json", ["clean_baseline", "nulls_injected", "duplicate_rows"]
    )
    started = api12.ok("suite_run", {"async": True}, suite=str(three), scale="tiny")
    done = wait(api12, started["job_id"])
    assert done["status"] == "succeeded" and len(done["result"]["scenarios"]) == 3
