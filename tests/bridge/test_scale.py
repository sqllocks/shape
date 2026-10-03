"""P6-11 deliverable 2 (scale_generate, stream, stream_status, stream_stop, scale_status,
scale_cancel): the same runs as `shape generate --scale-mode`, as jobs."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from fakes import LH, RUN, WS, FakeFabric

from shape.cli.main import main

ROWS = {"customer": 40, "order": 1200, "order_line": 3100}


def part_rows(out: Path, table: str) -> list[int]:
    return [
        pq.ParquetFile(p).metadata.num_rows for p in sorted((out / table).glob("part-*.parquet"))
    ]


def wait_final(api, job_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = api.ok("job_status", job_id=job_id)
        if state["status"] not in ("running", "submitted"):
            return state
        time.sleep(0.05)
    raise AssertionError("job did not finish")


# ---- scale_generate, local ------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["local_single", "local_mp"])
def test_scale_generate_writes_exact_rows_in_chunk_sized_parts(api, schema_file, tmp_path, mode):
    out = tmp_path / "out"
    result = api.ok(
        "scale_generate",
        domain=str(schema_file),
        scale_mode=mode,
        chunk_size=1000,
        sinks=["parquet"],
        sink_config={"parquet": {"output_dir": str(out)}},
    )
    assert result["rows_generated"] == sum(ROWS.values()) and result["scale_mode"] == mode
    assert result["sinks_written"] == {"parquet": "ok"}
    assert result["throughput_rows_per_sec"] > 0 and result["elapsed_seconds"] >= 0
    for table, rows in ROWS.items():
        parts = part_rows(out, table)
        assert sum(parts) == rows and max(parts) <= 1000


def test_scale_generate_equals_the_cli_run(api, capsys, schema_file, tmp_path):
    mine, theirs = tmp_path / "mine", tmp_path / "theirs"
    api.ok(
        "scale_generate",
        domain=str(schema_file),
        scale_mode="local_mp",
        chunk_size=1000,
        sinks=["parquet"],
        sink_config={"parquet": {"output_dir": str(mine)}},
    )
    code = main(
        [
            "generate",
            str(schema_file),
            "--scale-mode",
            "local_mp",
            "--chunk-size",
            "1000",
            "--sink",
            "parquet",
            "-o",
            str(theirs),
        ]
    )
    capsys.readouterr()
    assert code == 0
    for table in ROWS:
        assert sorted(p.name for p in (mine / table).iterdir()) == sorted(
            p.name for p in (theirs / table).iterdir()
        )
        for part in (mine / table).glob("part-*.parquet"):
            assert pq.read_table(part).equals(pq.read_table(theirs / table / part.name))


def test_scale_generate_defaults_to_local_mp_and_the_memory_sink(api, schema_file):
    result = api.ok("scale_generate", domain=str(schema_file))
    assert result["scale_mode"] == "local_mp" and result["sinks_written"] == {"memory": "ok"}
    assert result["rows_generated"] == sum(ROWS.values())


def test_scale_generate_a_domain(api):
    result = api.ok("scale_generate", domain="retail", scale="small", scale_mode="local_single")
    assert result["rows_generated"] == 21750 and result["scale"] == "small"


@pytest.mark.parametrize(
    "args, code",
    [
        ({"domain": "nope"}, "input.unknown_domain"),
        ({"domain": "retail", "scale": "gigantic"}, "input.invalid_value"),
        ({"domain": "retail", "scale_mode": "teleport"}, "usage.invalid_argument"),
        ({"domain": "retail", "sinks": ["warp"]}, "input.invalid_value"),
        ({"domain": "retail", "sinks": ["parquet"]}, "input.invalid_value"),
        ({"domain": "retail", "chunk_size": 0}, "usage.invalid_argument"),
        ({"domain": "retail", "sinks": "memory"}, "usage.invalid_argument"),
        ({"domain": "retail", "profile": "ghost"}, "input.invalid_value"),
    ],
)
def test_scale_generate_refusals_make_no_job(api, jobs_dir, args, code):
    api.fail("scale_generate", code, **args)
    assert (
        not list((jobs_dir / "bridge").glob("job-*.json"))
        if (jobs_dir / "bridge").exists()
        else True
    )


def test_scale_generate_as_a_job_and_scale_status(api, schema_file, tmp_path):
    out = tmp_path / "out"
    job = api.ok(
        "scale_generate",
        {"async": True},
        domain=str(schema_file),
        sinks=["parquet"],
        sink_config={"parquet": {"output_dir": str(out)}},
    )
    assert job["job_id"].startswith("job-") and job["command"] == "scale_generate"
    state = wait_final(api, job["job_id"])
    assert state["status"] == "succeeded" and state["result"]["rows_generated"] == sum(
        ROWS.values()
    )
    assert api.ok("scale_status", job_id=job["job_id"])["status"] == "succeeded"
    assert sum(part_rows(out, "order")) == ROWS["order"]


def test_scale_status_and_cancel_are_for_scale_jobs_only(api, schema_file):
    job = api.ok("generate", {"async": True}, domain="retail", scale="small")
    wait_final(api, job["job_id"])
    api.fail("scale_status", "input.unknown_job", job_id=job["job_id"])
    api.fail("scale_cancel", "input.unknown_job", job_id=job["job_id"])


def test_a_running_scale_job_can_be_cancelled(api, tmp_path):
    from scale_schemas import plain_doc

    big = tmp_path / "big.json"
    big.write_text(
        json.dumps(plain_doc({"customer": 100, "order": 600_000, "order_line": 600_000}))
    )
    out = tmp_path / "out"
    job = api.ok(
        "scale_generate",
        {"async": True},
        domain=str(big),
        scale_mode="local_single",
        chunk_size=10_000,
        sinks=["parquet"],
        sink_config={"parquet": {"output_dir": str(out)}},
    )
    deadline = time.time() + 30
    while time.time() < deadline and not api.ok("job_status", job_id=job["job_id"])["progress"].get(
        "rows_done"
    ):
        time.sleep(0.02)
    result = api.ok("scale_cancel", job_id=job["job_id"])
    assert result["cancelled"] is True and result["status"] == "cancelled"
    assert api.ok("job_status", job_id=job["job_id"])["status"] == "cancelled"
    assert sum(part_rows(out, "order")) < 600_000


# ---- scale_generate, fabric_spark -----------------------------------------------------------


@pytest.fixture
def fabric(monkeypatch):
    fake = FakeFabric()
    monkeypatch.setattr("shape.scale.http.urllib_transport", fake)
    monkeypatch.setenv("SHAPE_FABRIC_STORAGE_TOKEN", "stor-1")
    return fake


def submit(api, schema_file, token="tok-1", **extra):
    return api.ok(
        "scale_generate",
        domain=str(schema_file),
        scale_mode="fabric_spark",
        sinks=["lakehouse"],
        sink_config={"workspace_id": WS, "lakehouse_id": LH, "token": token},
        **extra,
    )


def test_fabric_spark_is_submitted_and_recorded_as_a_job(api, fabric, schema_file, jobs_dir):
    result = submit(api, schema_file)
    assert result["status"] == "submitted" and result["job_id"].startswith("job-")
    assert result["fabric_run_id"].startswith(RUN[:-1]) and result["total_rows_queued"] == sum(
        ROWS.values()
    )
    assert fabric.runs == 1 and fabric.uploaded_specs()[0]["row_counts"] == ROWS
    state = api.ok("job_status", job_id=result["job_id"], token="tok-1")
    assert state["status"] == "submitted" and state["command"] == "scale_generate"


def test_a_fabric_job_is_polled_for_its_state_and_survives_a_restart(
    api, fabric, schema_file, jobs_dir
):
    from bridge_helpers import Caller

    from shape.bridge.core import Bridge

    job_id = submit(api, schema_file)["job_id"]
    fabric.job_status = "InProgress"
    assert api.ok("scale_status", job_id=job_id, token="tok-1")["status"] == "running"
    restarted = Caller(Bridge(jobs_dir))
    fabric.job_status = "Completed"
    assert restarted.ok("scale_status", job_id=job_id, token="tok-1")["status"] == "succeeded"
    assert (
        Caller(Bridge(jobs_dir)).ok("job_status", job_id=job_id)["status"] == "succeeded"
    )  # no token needed now


def test_a_failed_fabric_run_carries_its_reason(api, fabric, schema_file):
    job_id = submit(api, schema_file)["job_id"]
    fabric.job_status = "Failed"
    fabric.failure = {"message": "out of memory"}
    state = api.ok("job_status", job_id=job_id, token="tok-1")
    assert state["status"] == "failed" and "out of memory" in state["error"]["message"]


def test_a_fabric_job_is_cancelled_in_fabric(api, fabric, schema_file):
    job_id = submit(api, schema_file)["job_id"]
    result = api.ok("scale_cancel", job_id=job_id, token="tok-1")
    assert (
        result["cancelled"] is True
        and result["status"] == "cancelled"
        and fabric.job_status == "Cancelled"
    )
    assert any(c["method"] == "POST" and c["url"].endswith("/cancel") for c in fabric.calls)
    again = api.ok("scale_cancel", job_id=job_id, token="tok-1")
    assert again["cancelled"] is False


def test_the_fabric_token_is_never_stored(api, fabric, schema_file, jobs_dir):
    job_id = submit(api, schema_file, token="tok-very-secret")["job_id"]
    api.ok("scale_status", job_id=job_id, token="tok-very-secret")
    api.ok("scale_cancel", job_id=job_id, token="tok-very-secret")
    files = [p for p in jobs_dir.rglob("*") if p.is_file()]
    assert files and not any("tok-very-secret" in p.read_text() for p in files)


def test_fabric_needs_a_token_and_the_ids(api, fabric, schema_file, monkeypatch):
    api.fail(
        "scale_generate",
        "auth.missing_credentials",
        domain=str(schema_file),
        scale_mode="fabric_spark",
        sink_config={"workspace_id": WS, "lakehouse_id": LH},
    )
    api.fail(
        "scale_generate",
        "input.invalid_value",
        domain=str(schema_file),
        scale_mode="fabric_spark",
        sink_config={"token": "t"},
    )
    job_id = submit(api, schema_file)["job_id"]
    api.fail("scale_status", "auth.missing_credentials", job_id=job_id)
    api.fail("scale_cancel", "auth.missing_credentials", job_id=job_id)
    monkeypatch.setenv("SHAPE_FABRIC_TOKEN", "tok-env")
    assert api.ok("scale_status", job_id=job_id)["status"] == "submitted"


def test_fabric_rejecting_the_token_is_an_auth_error(api, fabric, schema_file):
    fabric.queued_errors = [401]
    api.fail(
        "scale_generate",
        "auth.rejected",
        domain=str(schema_file),
        scale_mode="fabric_spark",
        sink_config={"workspace_id": WS, "lakehouse_id": LH, "token": "bad"},
    )


# ---- stream ---------------------------------------------------------------------------------


def stream(api, tmp_path=None, **extra):
    args = {
        "domain": "retail",
        "scale": "small",
        "seed": 3,
        "interval_seconds": 0,
        "chunk_size": 50,
    }
    args.update(extra)
    return api.ok("stream", **args)


def test_a_stream_writes_chunk_after_chunk_until_max_chunks(api, tmp_path):
    out = tmp_path / "stream"
    started = stream(
        api, max_chunks=3, sinks=["parquet"], sink_config={"parquet": {"output_dir": str(out)}}
    )
    assert started["status"] == "started" and started["stream_id"] == started["job_id"]
    state = wait_final(api, started["stream_id"])
    assert (
        state["status"] == "succeeded"
        and state["result"]["chunks_written"] == 3
        and state["result"]["stopped"] is False
    )
    status = api.ok("stream_status", stream_id=started["stream_id"])
    assert status["chunks_written"] == 3 and status["running"] is False and status["error"] is None
    assert status["rows_written"] == state["result"]["rows_written"] > 0
    assert sorted(p.name for p in out.iterdir()) == ["chunk-000000", "chunk-000001", "chunk-000002"]
    customers = [
        pq.read_table(next((out / c / "customer").glob("part-*.parquet")))
        for c in sorted(p.name for p in out.iterdir())
    ]
    assert all(t.num_rows == 50 for t in customers)  # chunk_size caps each table
    assert not customers[0].equals(customers[1])  # a new seed per chunk


def test_a_stream_chunks_are_reproducible_from_the_seed(api, tmp_path):
    first = stream(
        api,
        max_chunks=2,
        sinks=["parquet"],
        sink_config={"parquet": {"output_dir": str(tmp_path / "a")}},
    )["stream_id"]
    second = stream(
        api,
        max_chunks=2,
        sinks=["parquet"],
        sink_config={"parquet": {"output_dir": str(tmp_path / "b")}},
    )["stream_id"]
    wait_final(api, first), wait_final(api, second)
    for chunk in ("chunk-000000", "chunk-000001"):
        a = pq.read_table(next((tmp_path / "a" / chunk / "customer").glob("part-*.parquet")))
        b = pq.read_table(next((tmp_path / "b" / chunk / "customer").glob("part-*.parquet")))
        assert a.equals(b)


def test_a_running_stream_is_stopped(api):
    started = stream(api, interval_seconds=30)
    deadline = time.time() + 30
    while (
        time.time() < deadline
        and api.ok("stream_status", stream_id=started["stream_id"])["chunks_written"] < 1
    ):
        time.sleep(0.02)
    status = api.ok("stream_status", stream_id=started["stream_id"])
    assert (
        status["running"] is True
        and status["status"] == "running"
        and status["chunks_written"] >= 1
    )
    assert api.ok("stream_stop", stream_id=started["stream_id"]) == {
        "stream_id": started["stream_id"],
        "status": "stopped",
    }
    state = api.ok("stream_status", stream_id=started["stream_id"])
    assert state["running"] is False and state["status"] == "cancelled"
    assert state["chunks_written"] >= 1 and state["rows_written"] > 0  # what it did is kept
    assert (
        api.ok("stream_stop", stream_id=started["stream_id"])["status"] == "stopped"
    )  # again: still stopped


def test_stopping_a_stream_that_ended_by_itself(api):
    started = stream(api, max_chunks=1)
    wait_final(api, started["stream_id"])
    assert api.ok("stream_stop", stream_id=started["stream_id"])["status"] == "stopped"


def test_stream_ids_are_checked(api, schema_file):
    api.fail("stream_status", "input.unknown_job", stream_id="job-ffffffffffff")
    api.fail("stream_stop", "input.unknown_job", stream_id="job-ffffffffffff")
    job = api.ok("generate", {"async": True}, domain="retail", scale="small")
    wait_final(api, job["job_id"])
    api.fail("stream_status", "input.unknown_job", stream_id=job["job_id"])
    api.fail("stream_stop", "input.unknown_job", stream_id=job["job_id"])
    api.fail("stream_status", "usage.missing_argument")


@pytest.mark.parametrize(
    "args, code",
    [
        ({"domain": "nope"}, "input.unknown_domain"),
        ({"domain": "retail", "scale": "gigantic"}, "input.invalid_value"),
        ({"domain": "retail", "sinks": ["warp"]}, "input.invalid_value"),
        ({"domain": "retail", "sinks": ["parquet"]}, "input.invalid_value"),
        ({"domain": "retail", "max_chunks": 0}, "usage.invalid_argument"),
        ({"domain": "retail", "chunk_size": 0}, "usage.invalid_argument"),
    ],
)
def test_stream_refusals_make_no_job(api, jobs_dir, args, code):
    api.fail("stream", code, **args)
    assert not (jobs_dir / "bridge").exists() or not list((jobs_dir / "bridge").glob("job-*.json"))


@pytest.mark.parametrize(
    "command, args",
    [
        ("scale_generate", {"domain": "nope"}),
        ("scale_generate", {"domain": "retail", "sinks": ["warp"]}),
    ],
)
def test_an_async_scale_request_that_is_wrong_makes_no_job(api, jobs_dir, command, args):
    response = api.call(command, {"async": True}, **args)
    assert not response["ok"] and response["error"]["group"] == "input"
    assert not (jobs_dir / "bridge").exists() or not list((jobs_dir / "bridge").glob("job-*.json"))


def test_a_failing_sink_fails_the_stream_with_its_error(api, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    started = stream(
        api,
        max_chunks=2,
        sinks=["parquet"],
        sink_config={"parquet": {"output_dir": str(blocker / "x")}},
    )
    state = wait_final(api, started["stream_id"])
    assert state["status"] == "failed" and state["error"]["group"] in ("io", "input")
    status = api.ok("stream_status", stream_id=started["stream_id"])
    assert status["running"] is False and status["error"]
