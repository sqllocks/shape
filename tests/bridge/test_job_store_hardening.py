"""AUD-security2 #289: the bridge's job store keeps its directory private, survives one bad job
file, and takes only exact job ids."""

from __future__ import annotations

import json
import os
import stat
import sys
import time

import pytest

from shape.bridge.jobs import JOB_FORMAT, JOB_VERSION, now_iso

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")


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


def write_record(jobs_dir, job_id, **fields):
    record = {
        "format": JOB_FORMAT,
        "version": JOB_VERSION,
        "job_id": job_id,
        "command": "generate",
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


@posix_only
def test_an_existing_jobs_directory_is_made_private_before_a_job_is_written(api, jobs_dir):
    folder = jobs_dir / "bridge"
    folder.mkdir(parents=True)
    folder.chmod(0o777)
    wait_final(api, start_generate(api)["job_id"])
    assert stat.S_IMODE(os.stat(folder).st_mode) == 0o700


def test_a_deeply_nested_job_file_does_not_break_job_list(api, jobs_dir):
    write_record(jobs_dir, "job-000000000001", status="succeeded")
    (jobs_dir / "bridge" / "job-000000000002.json").write_text("[" * 100_000 + "]" * 100_000)
    assert [j["job_id"] for j in api.ok("job_list")["jobs"]] == ["job-000000000001"]
    api.fail("job_status", "input.invalid_schema", job_id="job-000000000002")


@pytest.mark.parametrize("missing", ["created_at", "status", "job_id"])
def test_a_record_missing_a_field_does_not_break_job_list(api, jobs_dir, missing):
    write_record(jobs_dir, "job-000000000001", status="succeeded")
    bad = write_record(jobs_dir, "job-000000000003", status="succeeded")
    del bad[missing]
    (jobs_dir / "bridge" / "job-000000000003.json").write_text(json.dumps(bad))
    assert [j["job_id"] for j in api.ok("job_list")["jobs"]] == ["job-000000000001"]
    api.fail("job_status", "input.invalid_schema", job_id="job-000000000003")


def test_a_job_id_with_a_trailing_newline_is_not_a_job_id(api, jobs_dir):
    write_record(jobs_dir, "job-aaaaaaaaaaaa", status="succeeded")
    api.fail("job_status", "input.unknown_job", job_id="job-aaaaaaaaaaaa\n")


def test_scale_and_demo_ids_take_no_trailing_newline():
    from shape.demo.home import check_name
    from shape.scale.jobs import JobNotFoundError, JobStore

    with pytest.raises(ValueError):
        check_name("session1\n", "session")
    with pytest.raises(JobNotFoundError):
        JobStore(__import__("pathlib").Path("unused"))._path("job1\n")
