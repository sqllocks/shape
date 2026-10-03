"""P6-11 deliverable 3: long-running commands return a job id; status, progress and cancel are
commands; jobs survive a restart of the bridge process."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

import pytest
from bridge_helpers import Caller

from shape.bridge.core import Bridge
from shape.bridge.jobs import JOB_FORMAT, JOB_VERSION, now_iso


def wait_final(api, job_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = api.ok("job_status", job_id=job_id)
        if state["status"] not in ("running", "submitted"):
            return state
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def start_generate(api):
    return api.ok("generate", {"async": True}, domain="retail", scale="small", seed=1)


def job_files(jobs_dir):
    return sorted((jobs_dir / "bridge").glob("job-*.json"))


def write_record(jobs_dir, job_id="job-aaaaaaaaaaaa", **fields):
    record = {
        "format": JOB_FORMAT,
        "version": JOB_VERSION,
        "job_id": job_id,
        "command": "generate",
        "status": "running",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "request": {"args": {}, "options": {}},
        "progress": {},
        "result": None,
        "error": None,
        "worker": {"pid": os.getpid()},
        "external": None,
        "cancellable": False,
        **fields,
    }
    folder = jobs_dir / "bridge"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{job_id}.json").write_text(json.dumps(record))
    return record


def dead_pid() -> int:
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait()
    return done.pid


# ---- the job file ---------------------------------------------------------------------------


def test_a_job_is_a_versioned_file_under_the_jobs_directory(api, jobs_dir):
    job = start_generate(api)
    wait_final(api, job["job_id"])
    (path,) = job_files(jobs_dir)
    assert path.name == f"{job['job_id']}.json"
    record = json.loads(path.read_text())
    assert record["format"] == "shape-bridge-job" and record["version"] == 1
    assert record["command"] == "generate" and record["status"] == "succeeded"
    assert record["request"]["args"]["domain"] == "retail" and record["request"]["options"] == {
        "async": True
    }
    assert record["result"]["total_rows"] > 0 and record["error"] is None
    assert record["worker"]["pid"] == os.getpid()
    assert (
        oct(path.stat().st_mode & 0o777) == "0o600"
        and oct(path.parent.stat().st_mode & 0o777) == "0o700"
    )
    assert not list(path.parent.glob(".job-*"))  # no temporary file left


def test_the_jobs_directory_is_configurable_and_defaults_from_the_environment(
    tmp_path, monkeypatch
):
    explicit = tmp_path / "explicit"
    api = Caller(Bridge(explicit))
    wait_final(api, start_generate(api)["job_id"])
    assert len(job_files(explicit)) == 1
    env_dir = tmp_path / "from-env"
    monkeypatch.setenv("SHAPE_JOBS_DIR", str(env_dir))
    api = Caller(Bridge())
    wait_final(api, start_generate(api)["job_id"])
    assert len(job_files(env_dir)) == 1 and len(job_files(explicit)) == 1


def test_secrets_never_reach_a_job_file(bridge, jobs_dir):
    from shape.bridge.context import Context
    from shape.bridge.jobs import mask_secrets

    args = {
        "domain": "d",
        "sink_config": {"a": {"password": "hunter2", "token": "tok-secret", "output_dir": "/out"}},
        "api_key": "key-secret",
        "connection_string": "Server=x;Password=y",
        "empty": {"token": ""},
        "keys": [{"access_key": "ak-secret", "name": "n"}],
    }
    masked = mask_secrets(args)
    assert masked["sink_config"]["a"] == {"password": "***", "token": "***", "output_dir": "/out"}
    assert (
        masked["api_key"] == "***"
        and masked["connection_string"] == "***"
        and masked["empty"] == {"token": ""}
    )
    assert masked["keys"] == [{"access_key": "***", "name": "n"}] and masked["domain"] == "d"
    record = bridge.jobs.start(
        "generate",
        args,
        {},
        lambda c: {"ok": 1},
        lambda cancel, progress: Context(bridge.jobs, cancel=cancel, progress=progress),
    )
    for _ in range(100):
        if bridge.jobs.get(record["job_id"])["status"] != "running":
            break
        time.sleep(0.02)
    text = job_files(jobs_dir)[0].read_text()
    assert not any(
        x in text for x in ("hunter2", "tok-secret", "key-secret", "ak-secret", "Password=y")
    )
    assert json.loads(text)["request"]["args"]["sink_config"]["a"]["output_dir"] == "/out"


# ---- status, list, and a restart ------------------------------------------------------------


def test_job_status_reports_state_progress_and_result(api):
    job = start_generate(api)
    assert set(job) >= {"job_id", "command", "status", "created_at", "progress", "result", "error"}
    state = wait_final(api, job["job_id"])
    assert state["status"] == "succeeded" and state["command"] == "generate"
    assert state["result"]["integrity_pass"] is True and state["error"] is None


def test_a_job_outlives_the_bridge_that_started_it(api, jobs_dir):
    job = start_generate(api)
    before = wait_final(api, job["job_id"])
    restarted = Caller(Bridge(jobs_dir))
    after = restarted.ok("job_status", job_id=job["job_id"])
    assert after == before
    listed = restarted.ok("job_list")
    assert [j["job_id"] for j in listed["jobs"]] == [job["job_id"]] and listed["count"] == 1


def test_a_failed_job_keeps_its_error(api, jobs_dir):
    job = api.ok("verify", {"async": True}, path=str(jobs_dir / "no-such-file.csv"))
    state = wait_final(api, job["job_id"])
    assert state["status"] == "failed" and state["result"] is None
    assert state["error"]["code"] == "input.not_found" and state["error"]["group"] == "input"
    assert (
        Caller(Bridge(jobs_dir)).ok("job_status", job_id=job["job_id"])["error"] == state["error"]
    )


def test_a_job_whose_process_died_is_interrupted(api, jobs_dir):
    write_record(jobs_dir, worker={"pid": dead_pid()})
    state = api.ok("job_status", job_id="job-aaaaaaaaaaaa")
    assert state["status"] == "interrupted" and state["error"]["code"] == "input.job_interrupted"
    assert (
        json.loads((jobs_dir / "bridge" / "job-aaaaaaaaaaaa.json").read_text())["status"]
        == "interrupted"
    )
    assert api.ok("job_status", job_id="job-aaaaaaaaaaaa")["status"] == "interrupted"  # and stays


def test_a_running_record_of_this_pid_with_no_live_worker_is_interrupted(api, jobs_dir):
    write_record(jobs_dir, worker={"pid": os.getpid()})
    assert api.ok("job_status", job_id="job-aaaaaaaaaaaa")["status"] == "interrupted"


def test_a_job_another_live_process_runs_stays_running_and_cannot_be_cancelled_from_here(
    api, jobs_dir
):
    write_record(jobs_dir, worker={"pid": os.getppid()}, cancellable=True)
    assert api.ok("job_status", job_id="job-aaaaaaaaaaaa")["status"] == "running"
    e = api.fail("job_cancel", "input.job_state", job_id="job-aaaaaaaaaaaa")
    assert str(os.getppid()) in e["message"]


def test_job_list_filters_by_status_and_limits(api, jobs_dir):
    for i in range(3):
        write_record(
            jobs_dir,
            f"job-00000000000{i}",
            status="succeeded",
            created_at=f"2026-01-0{i + 1}T00:00:00Z",
        )
    write_record(jobs_dir, "job-000000000009", status="failed", created_at="2026-02-01T00:00:00Z")
    everything = api.ok("job_list")
    assert [j["job_id"][-1] for j in everything["jobs"]] == ["0", "1", "2", "9"] and everything[
        "count"
    ] == 4
    assert [j["job_id"][-1] for j in api.ok("job_list", status="failed")["jobs"]] == ["9"]
    last = api.ok("job_list", limit=2)
    assert [j["job_id"][-1] for j in last["jobs"]] == ["2", "9"] and last["count"] == 4
    api.fail("job_list", "usage.invalid_argument", status="paused")
    api.fail("job_list", "usage.invalid_argument", limit=0)


def test_job_list_skips_files_it_cannot_read(api, jobs_dir):
    write_record(jobs_dir, "job-000000000001", status="succeeded")
    (jobs_dir / "bridge" / "job-000000000002.json").write_text("{ broken")
    assert [j["job_id"] for j in api.ok("job_list")["jobs"]] == ["job-000000000001"]


# ---- a file this Shape cannot read ----------------------------------------------------------


def test_a_job_file_from_a_newer_shape_is_refused_with_a_message_that_says_so(api, jobs_dir):
    write_record(jobs_dir, version=JOB_VERSION + 1, status="succeeded")
    e = api.fail("job_status", "input.unsupported_format_version", job_id="job-aaaaaaaaaaaa")
    assert f"version {JOB_VERSION + 1}" in e["message"] and "newer Shape" in e["message"]
    assert e["hint"] and "upgrade" in e["hint"]


@pytest.mark.parametrize(
    "mutation",
    [{"version": 0}, {"version": "1"}, {"version": True}, {"version": None}, {"format": "other"}],
)
def test_a_job_file_that_is_not_a_bridge_job_record_is_invalid(api, jobs_dir, mutation):
    write_record(jobs_dir, status="succeeded", **mutation)
    api.fail("job_status", "input.invalid_schema", job_id="job-aaaaaaaaaaaa")


def test_a_corrupt_job_file_is_invalid(api, jobs_dir):
    folder = jobs_dir / "bridge"
    folder.mkdir(parents=True)
    (folder / "job-bbbbbbbbbbbb.json").write_text("{ nope")
    api.fail("job_status", "input.invalid_schema", job_id="job-bbbbbbbbbbbb")


@pytest.mark.parametrize(
    "job_id", ["job-123", "../../etc/passwd", "job-AAAAAAAAAAAA", "x", "job-aaaaaaaaaaaa/../x"]
)
def test_an_id_that_is_not_a_job_id_never_touches_the_filesystem(api, job_id):
    for command in ("job_status", "job_cancel", "scale_status", "stream_status"):
        key = "stream_id" if command == "stream_status" else "job_id"
        api.fail(command, "input.unknown_job", **{key: job_id})


def test_an_unknown_job(api):
    api.fail("job_status", "input.unknown_job", job_id="job-ffffffffffff")


# ---- cancel ---------------------------------------------------------------------------------


def test_cancelling_a_finished_job_changes_nothing(api):
    job = start_generate(api)
    wait_final(api, job["job_id"])
    result = api.ok("job_cancel", job_id=job["job_id"])
    assert result["cancelled"] is False and result["status"] == "succeeded"


def test_a_job_that_cannot_notice_a_cancel_says_so(api, monkeypatch):
    gate = threading.Event()
    from shape.bridge.registry import COMMANDS

    def slow(args, ctx):
        gate.wait(10)
        return {"version": "x", "domains": [], "count": 0}

    cmd = COMMANDS["generate"]
    monkeypatch.setitem(COMMANDS, "generate", cmd.__class__(**{**cmd.__dict__, "handler": slow}))
    try:
        job = start_generate(api)
        e = api.fail("job_cancel", "input.job_state", job_id=job["job_id"])
        assert "cannot be cancelled" in e["message"]
        assert api.ok("job_status", job_id=job["job_id"])["status"] == "running"
    finally:
        gate.set()
    assert wait_final(api, job["job_id"])["status"] == "succeeded"


def test_a_recorded_active_job_whose_process_died_cannot_be_cancelled_it_is_interrupted(
    api, jobs_dir
):
    write_record(jobs_dir, worker={"pid": dead_pid()}, cancellable=True)
    result = api.ok("job_cancel", job_id="job-aaaaaaaaaaaa")
    assert result["status"] == "interrupted" and result["cancelled"] is False


def test_job_commands_need_an_id(api):
    for command in ("job_status", "job_cancel", "scale_status", "scale_cancel"):
        api.fail(command, "usage.missing_argument")


def test_progress_is_visible_while_a_job_runs(api, schema_file):
    from scale_schemas import plain_doc

    big = schema_file.parent / "big.json"
    big.write_text(
        json.dumps(plain_doc({"customer": 100, "order": 400_000, "order_line": 400_000}))
    )
    job = api.ok(
        "scale_generate",
        {"async": True},
        domain=str(big),
        scale_mode="local_single",
        chunk_size=20_000,
    )
    seen = 0
    deadline = time.time() + 60
    while time.time() < deadline:
        state = api.ok("job_status", job_id=job["job_id"])
        seen = max(seen, int(state["progress"].get("rows_done", 0)))
        if state["status"] != "running" or seen:
            break
        time.sleep(0.02)
    api.ok("job_cancel", job_id=job["job_id"])
    assert seen > 0
