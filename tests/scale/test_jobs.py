"""P6-13: job lifecycle: submit, status, cancel and resume (local and fabric_spark); the store,
the tracker and the stream manager."""

from __future__ import annotations

import json
import os
import threading
import time

import pyarrow.parquet as pq
import pytest
from fakes import LH, NB, RUN, WS, FakeFabric
from scale_schemas import plain_doc

from shape.scale.api import run_local, scale_generate
from shape.scale.http import HttpError
from shape.scale.jobs import (
    FabricJobTracker,
    JobNotFoundError,
    JobRecord,
    Jobs,
    JobStateError,
    JobStore,
    StreamManager,
)
from shape.scale.router import ScaleCancelled

TOKEN = "tok-secret-1234"


@pytest.fixture
def store(tmp_path):
    return JobStore(tmp_path / "jobs")


# ---- the store ------------------------------------------------------------------------------


def test_store_persists_across_instances(store, tmp_path):
    store.put(JobRecord("local-1", "local", request={"domain": "retail"}))
    store.update("local-1", status="running", progress={"rows_done": 5})
    other = JobStore(tmp_path / "jobs")  # another process, as far as the store can tell
    got = other.get("local-1")
    assert got.status == "running" and got.progress == {"rows_done": 5}
    assert [r.job_id for r in other.list()] == ["local-1"]
    other.update("local-1", status="succeeded")
    assert store.get("local-1").status == "succeeded"  # reads the newer file


def test_store_files_are_private_and_hold_no_token(store, tmp_path):
    store.put(JobRecord("spark-1", "fabric_spark", fabric={"workspace_id": WS}))
    path = tmp_path / "jobs" / "spark-1.json"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert not list((tmp_path / "jobs").glob(".job-*"))


@pytest.mark.parametrize("bad", ["../x", "a/b", "", "x" * 80, ".hidden", "a b"])
def test_store_rejects_ids_that_could_leave_the_directory(store, bad):
    with pytest.raises((JobNotFoundError, ValueError)):
        store.get(bad)
    with pytest.raises(ValueError):
        store.put(JobRecord(bad or "x y", "local"))


def test_store_unknown_job_and_delete(store):
    with pytest.raises(JobNotFoundError):
        store.get("nope")
    store.put(JobRecord("a", "local"))
    store.delete("a")
    with pytest.raises(JobNotFoundError):
        store.get("a")
    with pytest.raises(AttributeError):
        store.put(JobRecord("b", "local"))
        store.update("b", bogus=1)


def test_a_memory_store_works_without_a_directory():
    mem = JobStore()
    mem.put(JobRecord("m", "local"))
    assert mem.get("m").status == "submitted" and mem.root is None


# ---- local jobs: submit, status, cancel, resume ---------------------------------------------


def fake_run(release: threading.Event | None = None, fail: bool = False):
    calls = []

    def run(request, cancel, progress, resume):
        calls.append({"request": request, "resume": resume})
        progress({"rows_done": 1, "rows_total": 10})
        if release is not None:
            while not release.is_set():
                if cancel.is_set():
                    raise ScaleCancelled("cancelled")
                time.sleep(0.01)
        if fail:
            raise RuntimeError("boom")
        return {"rows_generated": 10}

    run.calls = calls  # type: ignore[attr-defined]
    return run


def test_submit_wait_status(store):
    jobs = Jobs(store)
    run = fake_run()
    job = jobs.start_local({"domain": "x"}, run, wait=True)
    assert job["status"] == "succeeded" and job["result"] == {"rows_generated": 10}
    assert job["progress"] == {"rows_done": 10, "rows_total": 10}
    assert jobs.status(job["job_id"])["status"] == "succeeded"
    assert run.calls == [{"request": {"domain": "x"}, "resume": False}]


def test_background_job_runs_then_cancels(store):
    jobs = Jobs(store)
    release = threading.Event()
    job = jobs.start_local({}, fake_run(release))
    job_id = job["job_id"]
    for _ in range(200):
        if jobs.status(job_id)["status"] == "running":
            break
        time.sleep(0.01)
    assert jobs.status(job_id)["status"] == "running"
    out = jobs.cancel(job_id)
    assert out["cancelled"] is True and out["status"] == "cancelled" and out["resumable"] is True
    assert jobs.cancel(job_id)["cancelled"] is False  # already final


def test_a_failed_job_records_the_error_and_resumes(store):
    jobs = Jobs(store)
    first = jobs.start_local({"domain": "x"}, fake_run(fail=True), wait=True)
    assert first["status"] == "failed" and "boom" in first["error"] and first["resumable"]
    ok = fake_run()
    again = jobs.start_local({}, ok, job_id=first["job_id"], wait=True)
    assert again["status"] == "succeeded" and again["attempts"] == 2 and again["error"] is None
    assert ok.calls == [
        {"request": {"domain": "x"}, "resume": True}
    ]  # the stored request, resume on
    with pytest.raises(JobStateError, match="only a failed or cancelled"):
        jobs.start_local({}, ok, job_id=first["job_id"])


def test_a_job_survives_a_restart_and_a_dead_run_can_be_cancelled_and_resumed(store, tmp_path):
    jobs = Jobs(store)
    record = store.put(JobRecord("local-dead", "local", status="running", request={"domain": "x"}))
    new_process = Jobs(JobStore(tmp_path / "jobs"))
    assert new_process.status(record.job_id)["status"] == "running"  # nothing runs it here
    out = new_process.cancel(record.job_id)
    assert out["status"] == "cancelled" and "no live run" in out["error"]
    done = new_process.start_local({}, fake_run(), job_id=record.job_id, wait=True)
    assert done["status"] == "succeeded"
    assert jobs.status(record.job_id)["status"] == "succeeded"


def test_resume_refuses_a_job_of_another_kind(store):
    store.put(JobRecord("spark-x", "fabric_spark", status="failed"))
    with pytest.raises(JobStateError, match="not a local job"):
        Jobs(store).start_local({}, fake_run(), job_id="spark-x")


def test_resume_of_masked_settings_needs_them_again(store):
    store.put(
        JobRecord(
            "local-m",
            "local",
            status="failed",
            request={"sink_config": {"warehouse": {"client_secret": "***"}}},
        )  # fmt: skip
    )
    with pytest.raises(JobStateError, match="masked"):
        Jobs(store).start_local({}, fake_run(), job_id="local-m")
    run = fake_run()
    Jobs(store).start_local(
        {}, run, job_id="local-m", wait=True,
        overrides={"sink_config": {"warehouse": {"client_secret": "again"}}},
    )  # fmt: skip
    assert run.calls[0]["request"]["sink_config"]["warehouse"]["client_secret"] == "again"


# ---- a real local job: cancel part way, resume to the same files ----------------------------


def real_params(tmp_path, out, **extra):
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps(plain_doc({"customer": 40, "order": 1500, "order_line": 6000})))
    return {
        "domain": str(schema), "scale_mode": "local_mp", "seed": 3, "chunk_size": 500,
        "sinks": ["parquet"], "sink_config": {"parquet": {"output_dir": str(out)}}, **extra,
    }  # fmt: skip


def read_all(out):
    return {
        d.name: sorted(
            i
            for p in sorted(d.glob("part-*.parquet"))
            for i in pq.read_table(p).column(0).to_pylist()
        )
        for d in sorted(out.iterdir())
        if d.is_dir()
    }


def test_cancel_then_resume_gives_the_files_of_an_uninterrupted_run(store, tmp_path):
    jobs = Jobs(store)
    out = tmp_path / "out"
    params = real_params(tmp_path, out)

    def run(req, cancel, progress, resume):
        def stop_early(info):
            progress(info)
            if info["rows_done"] >= 1500:
                cancel.set()

        return run_local(req, cancel, stop_early, resume)

    from shape.scale.api import normalize

    first = jobs.start_local(normalize(params), run, wait=True)
    assert first["status"] == "cancelled" and first["resumable"]
    partial = {d.name for d in out.iterdir() if (d / "_COMPLETE").exists()}
    assert partial != {"customer", "order", "order_line"}  # it did not finish
    done = jobs.start_local(
        {}, lambda r, c, p, resume: run_local(r, c, p, resume), job_id=first["job_id"], wait=True
    )
    assert done["status"] == "succeeded" and done["attempts"] == 2
    assert done["result"]["tables"] == {"customer": 40, "order": 1500, "order_line": 6000}
    reference = tmp_path / "ref"
    scale_generate(real_params(tmp_path, reference))
    assert read_all(out) == read_all(reference)


def test_scale_generate_records_a_job_and_masks_secrets(store, tmp_path):
    jobs = Jobs(store)
    out = tmp_path / "o"
    params = real_params(tmp_path, out)
    result = scale_generate(params, jobs=jobs)
    assert result["status"] == "succeeded" and result["rows_generated"] == 7540
    stored = store.get(result["job_id"])
    assert stored.result["rows_generated"] == 7540
    assert scale_generate(params, jobs=jobs, background=True)["job_id"] != result["job_id"]
    with pytest.raises(ValueError, match="background needs a job store"):
        scale_generate(params, background=True)


def test_scale_generate_request_checks(tmp_path):
    with pytest.raises(ValueError, match="unknown scale_generate"):
        scale_generate({"domain": "x", "bogus": 1})
    with pytest.raises(ValueError, match="name a domain"):
        scale_generate({})
    with pytest.raises(ValueError, match="unknown scale_mode"):
        scale_generate({"domain": "x", "scale_mode": "gpu"})
    with pytest.raises(ValueError, match="non-empty list"):
        scale_generate({"domain": "x", "sinks": []})
    with pytest.raises(ValueError, match="unknown scale"):
        scale_generate({**real_params(tmp_path, tmp_path / "o"), "scale": "huge"})


# ---- fabric_spark: status, cancel, resume ---------------------------------------------------


def spark_job(store, status="submitted", **extra):
    return store.put(
        JobRecord(
            "spark-t1",
            "fabric_spark",
            status=status,
            request={"domain": "retail"},
            fabric={
                "workspace_id": WS,
                "lakehouse_id": LH,
                "notebook_item_id": NB,
                "fabric_run_id": RUN,
            },
            **extra,
        )  # fmt: skip
    )


def test_tracker_maps_fabric_status_names_and_failure_reason():
    fake = FakeFabric()
    tracker = FabricJobTracker(TOKEN, fake)
    for raw, mapped in [
        ("NotStarted", "submitted"), ("InProgress", "running"), ("Deduplicating", "running"),
        ("Completed", "succeeded"), ("Failed", "failed"), ("Cancelled", "cancelled"),
        ("Weird", "weird"),
    ]:  # fmt: skip
        fake.job_status = raw
        assert tracker.get_status(WS, NB, RUN)["status"] == mapped
    fake.job_status, fake.failure = "Failed", {"message": "out of memory"}
    assert tracker.get_status(WS, NB, RUN)["error"] == "out of memory"
    assert all(c["headers"]["Authorization"] == f"Bearer {TOKEN}" for c in fake.calls)


def test_status_polls_fabric_and_updates_the_record(store):
    fake = FakeFabric()
    jobs = Jobs(store, transport=fake)
    spark_job(store)
    fake.job_status = "InProgress"
    assert jobs.status("spark-t1", TOKEN)["status"] == "running"
    fake.job_status = "Completed"
    assert jobs.status("spark-t1", TOKEN)["status"] == "succeeded"
    polls = len(fake.calls)
    assert jobs.status("spark-t1")["status"] == "succeeded"  # final: no more calls, no token
    assert len(fake.calls) == polls


def test_status_needs_a_token_while_the_job_is_active(store, monkeypatch):
    monkeypatch.delenv("SHAPE_FABRIC_TOKEN", raising=False)
    spark_job(store)
    with pytest.raises(ValueError, match="SHAPE_FABRIC_TOKEN"):
        Jobs(store, transport=FakeFabric()).status("spark-t1")
    monkeypatch.setenv("SHAPE_FABRIC_TOKEN", TOKEN)
    assert Jobs(store, transport=FakeFabric()).status("spark-t1")["status"] == "submitted"


def test_cancel_calls_fabric_once_and_marks_the_job(store):
    fake = FakeFabric()
    jobs = Jobs(store, transport=fake)
    spark_job(store, status="running")
    out = jobs.cancel("spark-t1", TOKEN)
    assert out["cancelled"] is True and out["status"] == "cancelled" and out["resumable"]
    assert [m for m, u in fake.methods() if u.endswith("/cancel")] == ["POST"]
    assert jobs.cancel("spark-t1", TOKEN)["cancelled"] is False
    assert sum(u.endswith("/cancel") for _, u in fake.methods()) == 1


def test_resume_reattaches_to_an_active_run_and_resubmits_an_ended_one(store):
    jobs = Jobs(store, transport=FakeFabric())
    spark_job(store, status="running")
    submitted = []
    out = jobs.resume_spark("spark-t1", TOKEN, lambda req: submitted.append(req) or {})
    assert submitted == [] and out["status"] == "submitted"  # polled: Fabric says NotStarted

    store.update("spark-t1", status="failed", error="oom")
    out = jobs.resume_spark(
        "spark-t1", TOKEN, lambda req: submitted.append(req) or {"fabric_run_id": "new-run"}
    )
    assert submitted == [{"domain": "retail"}]
    assert out["status"] == "submitted" and out["attempts"] == 2 and out["error"] is None
    assert out["fabric"]["fabric_run_id"] == "new-run" and out["fabric"]["workspace_id"] == WS

    store.update("spark-t1", status="succeeded")
    with pytest.raises(JobStateError, match="already succeeded"):
        jobs.resume_spark("spark-t1", TOKEN, lambda req: {})
    store.put(JobRecord("local-z", "local", status="failed"))
    with pytest.raises(JobStateError, match="not a fabric_spark"):
        jobs.resume_spark("local-z", TOKEN, lambda req: {})


def test_no_token_is_ever_written_to_the_store(store, tmp_path):
    fake = FakeFabric()
    jobs = Jobs(store, transport=fake)
    spark_job(store, status="running")
    jobs.status("spark-t1", TOKEN)
    jobs.cancel("spark-t1", TOKEN)
    for path in (tmp_path / "jobs").iterdir():
        assert TOKEN not in path.read_text()


def test_http_errors_and_retries():
    fake = FakeFabric()
    tracker = FabricJobTracker(TOKEN, fake)
    fake.queued_errors = [429, 503]
    assert tracker.get_status(WS, NB, RUN)["status"] == "submitted"  # retried twice, then ok
    assert len(fake.calls) == 3
    with pytest.raises(HttpError, match="HTTP 404"):
        tracker.get_status(WS, NB, "no-such-run")


def test_job_describe_never_leaks_request_internals(store):
    spark_job(store)
    desc = Jobs.describe(store.get("spark-t1"))
    assert set(desc) >= {
        "job_id",
        "kind",
        "status",
        "progress",
        "result",
        "error",
        "resumable",
        "fabric",
    }
    assert "request" not in desc


# ---- streams --------------------------------------------------------------------------------


def test_stream_manager_start_status_stop():
    mgr = StreamManager()

    def run(state):
        while not state.stop_event.is_set():
            state.record(7)
            state.stop_event.wait(0.01)

    sid = mgr.start(run)
    time.sleep(0.1)
    status = mgr.status(sid)
    assert status["running"] and status["rows_written"] >= 7 and status["rows_written"] % 7 == 0
    assert status["chunks_written"] * 7 == status["rows_written"]  # rows actually written
    assert mgr.stop(sid) is True
    assert "error" in mgr.status(sid) and "unknown" in mgr.status(sid)["error"]
    assert mgr.stop(sid) is None


def test_stream_manager_reports_an_error_and_keeps_a_stuck_stream_listed():
    mgr = StreamManager()
    sid = mgr.start(lambda state: (_ for _ in ()).throw(RuntimeError("source gone")))
    for _ in range(100):
        if not mgr.status(sid)["running"]:
            break
        time.sleep(0.01)
    assert "source gone" in mgr.status(sid)["error"]

    release = threading.Event()
    stuck = mgr.start(lambda state: release.wait(5))  # ignores stop_event
    assert mgr.stop(stuck, timeout=0.05) is False
    assert mgr.status(stuck)["running"]  # still listed, so it can be stopped again
    release.set()
    assert mgr.stop(stuck, timeout=2) is True


def test_stream_manager_instance_is_one_per_process():
    assert StreamManager.instance() is StreamManager.instance()


def test_default_jobs_dir_follows_the_environment(monkeypatch, tmp_path):
    from shape.scale.jobs import default_jobs_dir

    monkeypatch.setenv("SHAPE_JOBS_DIR", str(tmp_path / "j"))
    assert default_jobs_dir() == tmp_path / "j"
    monkeypatch.delenv("SHAPE_JOBS_DIR")
    assert default_jobs_dir() == type(tmp_path)(os.path.expanduser("~")) / ".shape" / "jobs"


def test_cancel_of_a_run_that_finished_first_reports_it_was_not_cancelled(store):
    jobs = Jobs(store)
    gate = threading.Event()

    def run(request, cancel, progress, resume):
        gate.wait(5)  # ignores the cancel request: it finishes first
        return {"rows_generated": 3}

    job = jobs.start_local({}, run)
    timer = threading.Timer(0.05, gate.set)
    timer.start()
    out = jobs.cancel(job["job_id"])
    timer.join()
    assert out["status"] == "succeeded" and out["cancelled"] is False


def test_a_cancel_from_another_process_stops_the_run_between_chunks(tmp_path):
    # Regression #486: the other process's cancel was recorded, the run never read it and the
    # job ended "succeeded".
    runner = Jobs(JobStore(tmp_path))  # the process running the job
    other = Jobs(JobStore(tmp_path))  # `shape jobs cancel` in a second terminal
    started = threading.Event()
    steps: list[int] = []

    def run(req, cancel, progress, resume):
        started.set()
        for i in range(200):
            if cancel.is_set():
                raise ScaleCancelled("the run was cancelled")
            steps.append(i)
            progress({"rows_done": i})
            time.sleep(0.02)
        return {"rows_generated": 200}

    job = runner.start_local({"domain": "x"}, run)
    started.wait()
    time.sleep(0.1)
    assert other.cancel(job["job_id"])["cancelled"] is True
    final = runner.wait(job["job_id"], timeout=30)
    assert final["status"] == "cancelled"
    assert len(steps) < 200
    assert JobStore(tmp_path).get(job["job_id"]).status == "cancelled"
