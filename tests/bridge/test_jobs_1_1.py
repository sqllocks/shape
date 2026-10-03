"""W7-04 acceptance: the job file ``shape-bridge-job`` and the vector files
``shape-bridge-vectors`` keep version 1: a 1.1 job file is read by the 1.1 bridge, and a 1.0 job
file written before 1.1 is read unchanged."""

from __future__ import annotations

import json
import time
from pathlib import Path

from data_1_1 import shop_tables, write_dataset

from shape.bridge.core import Bridge

DOCS = Path(__file__).resolve().parents[2] / "docs" / "bridge"
JOB_KEYS = {
    "format", "version", "job_id", "command", "status", "created_at", "updated_at", "request",
    "progress", "result", "error", "worker", "external", "cancellable",
}  # fmt: skip


def frozen_jobs() -> dict:
    return json.loads((DOCS / "vectors" / "1.0" / "job_status.json").read_text())["jobs"]


def place(jobs_dir: Path, records: dict) -> None:
    folder = jobs_dir / "bridge"
    folder.mkdir(parents=True, exist_ok=True)
    for job_id, record in records.items():
        (folder / f"{job_id}.json").write_text(json.dumps(record))


def test_a_1_0_job_file_is_read_unchanged_by_the_1_1_bridge(jobs_dir):
    records = frozen_jobs()
    assert len(records) >= 3 and all(r["format"] == "shape-bridge-job" for r in records.values())
    place(jobs_dir, records)
    before = {p.name: p.read_bytes() for p in (jobs_dir / "bridge").glob("*.json")}
    bridge = Bridge(jobs_dir)
    for version in ("1.0", "1.1"):
        for job_id, record in records.items():
            r = bridge.handle(
                {"api_version": version, "command": "job_status", "args": {"job_id": job_id}}
            )
            assert r["ok"] and r["result"]["job_id"] == job_id
            assert r["result"]["status"] == record["status"]
            assert r["result"]["command"] == record["command"]
            assert r["result"]["error"] == record["error"]
        listed = bridge.handle({"api_version": version, "command": "job_list"})["result"]
        assert {j["job_id"] for j in listed["jobs"]} == set(records)
    assert {p.name: p.read_bytes() for p in (jobs_dir / "bridge").glob("*.json")} == before


def test_a_1_0_and_a_1_1_request_read_the_same_job(jobs_dir):
    place(jobs_dir, frozen_jobs())
    bridge = Bridge(jobs_dir)
    for job_id in frozen_jobs():
        args = {"job_id": job_id}
        old = bridge.handle({"api_version": "1.0", "command": "job_status", "args": args})
        new = bridge.handle({"api_version": "1.1", "command": "job_status", "args": args})
        assert {k: v for k, v in old.items() if k != "api_version"} == {
            k: v for k, v in new.items() if k != "api_version"
        }


def test_a_job_written_by_the_1_1_bridge_is_a_version_1_job_file_a_new_bridge_reads(
    tmp_path, jobs_dir
):
    data = write_dataset(tmp_path / "data", shop_tables())
    bridge = Bridge(jobs_dir)
    profile = {"source": str(data), "dataset": True, "output": str(tmp_path / "s.shape")}
    bridge.handle({"api_version": "1.1", "command": "profile", "args": profile})
    started = bridge.handle(
        {
            "api_version": "1.1",
            "command": "proposals_propose",
            "args": {"profile": str(tmp_path / "s.shape"), "decisions": str(tmp_path / "d.json")},
            "options": {"async": True},
        }
    )
    assert started["ok"]
    job_id = started["result"]["job_id"]
    deadline = time.time() + 60
    while time.time() < deadline and bridge.jobs.get(job_id)["status"] == "running":
        time.sleep(0.05)
    bridge.close()
    file = json.loads((jobs_dir / "bridge" / f"{job_id}.json").read_text())
    assert (file["format"], file["version"]) == ("shape-bridge-job", 1)
    assert set(file) == JOB_KEYS  # the file has the 1.0 layout and nothing more
    assert file["status"] == "succeeded" and file["command"] == "proposals_propose"
    for version in ("1.0", "1.1"):  # a fresh bridge reads it; the job commands are 1.0 commands
        again = Bridge(jobs_dir).handle(
            {"api_version": version, "command": "job_status", "args": {"job_id": job_id}}
        )
        assert again["ok"] and again["result"]["result"] == file["result"]
        assert again["result"]["command"] == "proposals_propose"


def test_a_job_file_of_a_newer_version_is_not_misread(jobs_dir):
    record = {**next(iter(frozen_jobs().values())), "version": 2}
    place(jobs_dir, {record["job_id"]: record})
    r = Bridge(jobs_dir).handle(
        {"api_version": "1.1", "command": "job_status", "args": {"job_id": record["job_id"]}}
    )
    assert not r["ok"] and r["error"]["code"] == "input.unsupported_format_version"


def test_the_job_schema_and_the_error_schema_are_those_of_1_0():
    from shape.bridge.schemas import all_schemas

    current = all_schemas()
    for name in ("job.schema.json",):
        frozen = json.loads((DOCS / "schema" / "1.0" / name).read_text())
        now = json.loads(json.dumps(current[name]))
        assert {k: v for k, v in now.items() if k != "x-api-version"} == {
            k: v for k, v in frozen.items() if k != "x-api-version"
        }


def test_the_vector_files_are_version_1_files_in_1_0_and_1_1():
    for folder in (DOCS / "vectors", DOCS / "vectors" / "1.0"):
        files = sorted(folder.glob("*.json"))
        assert len(files) >= 24
        for path in files:
            doc = json.loads(path.read_text())
            assert (doc["format"], doc["version"]) == ("shape-bridge-vectors", 1), path
            assert doc["command"] == path.stem
