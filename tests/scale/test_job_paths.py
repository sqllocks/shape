"""A job record keeps absolute local paths, so a resume from another folder resumes
(HUNT2-fabric #639)."""

from __future__ import annotations

import json
from pathlib import Path

from shape.scale.api import normalize, run_local, scale_generate
from shape.scale.jobs import Jobs, JobStore


def _params(output: str) -> dict:
    return {
        "domain": "retail",
        "scale": "small",
        "scale_mode": "local_single",
        "sinks": ["parquet"],
        "sink_config": {"parquet": {"output_dir": output}},
        "chunk_size": 2000,
    }


def test_a_relative_output_dir_is_recorded_as_an_absolute_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    request = normalize(_params("out"))
    assert request["sink_config"]["parquet"]["output_dir"] == str(tmp_path / "out")


def test_normalize_does_not_change_the_caller_s_settings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    params = _params("out")
    normalize(params)
    assert params["sink_config"]["parquet"]["output_dir"] == "out"


def test_a_remote_base_path_is_left_alone(tmp_path):
    params = {
        "domain": "retail",
        "scale": "small",
        "sinks": ["lakehouse"],
        "sink_config": {"lakehouse": {"base_path": "abfss://c@a.dfs.core.windows.net/x"}},
    }
    got = normalize(params)["sink_config"]["lakehouse"]["base_path"]
    assert got == "abfss://c@a.dfs.core.windows.net/x"


def test_a_resume_from_another_folder_continues_in_the_first_one(tmp_path, monkeypatch):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    store = JobStore(tmp_path / "jobs")
    jobs = Jobs(store)
    monkeypatch.chdir(first)
    done = scale_generate(_params("out"), jobs=jobs)
    assert done["status"] == "succeeded"
    stored = json.loads((tmp_path / "jobs" / f"{done['job_id']}.json").read_text())
    assert Path(stored["request"]["sink_config"]["parquet"]["output_dir"]).is_absolute()
    store.update(done["job_id"], status="failed")

    monkeypatch.chdir(second)

    def run(req, cancel, progress, resume):
        return run_local(req, cancel, progress, resume)

    again = jobs.start_local({}, run, job_id=done["job_id"], wait=True)
    assert again["status"] == "succeeded"
    assert not (second / "out").exists()
    assert again["result"]["parts_skipped"] > 0
