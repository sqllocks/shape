"""#544: a job file is the job its name says, and a record with fields of the wrong type is
refused (and skipped by `job_list`) instead of answering for another job or failing the list."""

from __future__ import annotations

import json
import time

import pytest


@pytest.fixture
def finished(api, jobs_dir):
    job_id = api.ok("generate", {"async": True}, domain="retail", scale="small")["job_id"]
    deadline = time.time() + 60
    while api.ok("job_status", job_id=job_id)["status"] == "running" and time.time() < deadline:
        time.sleep(0.05)
    return job_id, jobs_dir / "bridge"


def test_a_copied_job_file_is_not_answered_as_its_original(api, finished):
    job_id, folder = finished
    copy = folder / "job-bbbbbbbbbbbb.json"
    copy.write_text((folder / f"{job_id}.json").read_text())
    e = api.fail("job_status", "input.invalid_schema", job_id="job-bbbbbbbbbbbb")
    assert "job-bbbbbbbbbbbb" in e["message"]
    assert [j["job_id"] for j in api.ok("job_list")["jobs"]] == [job_id]


@pytest.mark.parametrize(
    "field, value", [("worker", "x"), ("progress", [1]), ("external", "x"), ("command", 3)]
)
def test_a_field_of_the_wrong_type_is_refused_and_skipped(api, finished, field, value):
    job_id, folder = finished
    record = json.loads((folder / f"{job_id}.json").read_text())
    record.update(job_id="job-cccccccccccc", status="running", **{field: value})
    (folder / "job-cccccccccccc.json").write_text(json.dumps(record))
    api.fail("job_status", "input.invalid_schema", job_id="job-cccccccccccc")
    assert [j["job_id"] for j in api.ok("job_list")["jobs"]] == [job_id]
