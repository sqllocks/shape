"""#537: concurrent `profile` jobs (threads of one bridge) write their artifacts through files of
their own, so an artifact named by a content id holds exactly that content."""

from __future__ import annotations

import threading

import shape
from bridge_helpers import write_csv


def test_concurrent_profile_jobs_write_their_own_artifact(api, bridge, tmp_path, monkeypatch):
    sources = [write_csv(tmp_path / f"t{n}.csv", shift=n, rows=200 + 50 * n) for n in range(2)]
    real_save = shape.save
    both_in_save = threading.Barrier(2, timeout=30)
    targets: list[str] = []

    def save(profile, path, *args, **kwargs):
        targets.append(str(path))
        both_in_save.wait()  # the two jobs are inside save at the same time
        return real_save(profile, path, *args, **kwargs)

    monkeypatch.setattr(shape, "save", save)
    jobs = [api.ok("profile", {"async": True}, source=str(s))["job_id"] for s in sources]
    bridge.close()
    assert len(set(targets)) == 2, targets
    for job_id, source in zip(jobs, sources, strict=True):
        job = api.ok("job_status", job_id=job_id)
        assert job["status"] == "succeeded", job
        result = job["result"]
        loaded = shape.load(result["path"])
        assert loaded.name == source.stem == result["name"]
        assert result["path"].endswith(result["content_id"].replace(":", "-") + ".shape")
