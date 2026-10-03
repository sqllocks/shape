"""P6-13: `shape generate --scale-mode` per mode, and `shape jobs`, end to end."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from shape.cli.main import main

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scale"))
from fakes import LH, NB, RUN, WS, FakeFabric  # noqa: E402
from scale_schemas import plain_doc  # noqa: E402

ROWS = {"customer": 40, "order": 1200, "order_line": 3100}


@pytest.fixture(autouse=True)
def _confirm_remote(monkeypatch):
    # These tests are about the sinks, not the confirmation (tests/cli/test_remote_confirmation.py).
    monkeypatch.setenv("SHAPE_CONFIRM_REMOTE", "1")


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def schema_file(tmp_path):
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(plain_doc(ROWS)))
    return path


@pytest.fixture(autouse=True)
def jobs_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SHAPE_JOBS_DIR", str(tmp_path / "jobs"))
    return tmp_path / "jobs"


def part_rows(out: Path, table: str) -> list[int]:
    return [
        pq.ParquetFile(p).metadata.num_rows for p in sorted((out / table).glob("part-*.parquet"))
    ]


# ---- each mode ------------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["local_single", "local_mp"])
def test_scale_mode_writes_exact_rows_in_chunk_sized_parts(capsys, tmp_path, schema_file, mode):
    out = tmp_path / "out"
    code, text, _ = run(
        capsys,
        "generate",
        schema_file,
        "--scale-mode",
        mode,
        "--chunk-size",
        1000,
        "-o",
        out,
        "--json",
    )
    doc = json.loads(text)
    assert code == 0 and doc["scale_mode"] == mode and doc["tables"] == ROWS
    assert doc["sinks_written"] == {"parquet": "ok"} and doc["threads"] == (
        1 if mode == "local_single" else doc["threads"]
    )
    assert part_rows(out, "order_line") == [1000, 1000, 1000, 100]
    assert part_rows(out, "customer") == [40]
    code, text, _ = run(capsys, "jobs", "status", doc["job_id"], "--json")
    assert code == 0 and json.loads(text)["status"] == "succeeded"


def test_the_two_local_modes_write_identical_files(capsys, tmp_path, schema_file):
    for mode in ("local_single", "local_mp"):
        assert (
            run(
                capsys,
                "generate",
                schema_file,
                "--scale-mode",
                mode,
                "--chunk-size",
                700,
                "-o",
                tmp_path / mode,
            )[0]
            == 0
        )
    for table in ROWS:
        a = sorted((tmp_path / "local_single" / table).glob("part-*.parquet"))
        b = sorted((tmp_path / "local_mp" / table).glob("part-*.parquet"))
        assert [p.name for p in a] == [p.name for p in b]
        assert all(pq.read_table(x).equals(pq.read_table(y)) for x, y in zip(a, b, strict=True))


def test_retail_through_both_local_modes(capsys, tmp_path):
    pytest.importorskip("shape_domains")
    for mode in ("local_single", "local_mp"):
        code, text, _ = run(
            capsys, "generate", "retail", "--scale", "small", "--seed", 7, "--scale-mode", mode,
            "-o", tmp_path / mode, "--json",
        )  # fmt: skip
        doc = json.loads(text)
        assert code == 0 and doc["rows_generated"] == 21750
        assert doc["tables"]["order_line"] == 12500 and doc["tables"]["order"] == 5000


def test_processes_make_the_part_files(capsys, tmp_path, schema_file):
    code, text, _ = run(
        capsys, "generate", schema_file, "--scale-mode", "local_mp", "--processes", 2,
        "--chunk-size", 1000, "-o", tmp_path / "p", "--json",
    )  # fmt: skip
    doc = json.loads(text)
    assert code == 0 and doc["processes"] == 2 and doc["tables"] == ROWS
    assert part_rows(tmp_path / "p", "order") == [1000, 200]


def test_several_sinks_at_once(capsys, tmp_path, schema_file):
    code, text, _ = run(
        capsys, "generate", schema_file, "--scale-mode", "local_mp", "--sink", "parquet",
        "--sink", "memory", "--sink-config", f"parquet.output_dir={tmp_path / 'p'}", "--json",
    )  # fmt: skip
    assert code == 0 and set(json.loads(text)["sinks_written"]) == {"parquet", "memory"}
    assert part_rows(tmp_path / "p", "order") == [1200]


def test_human_summary_and_memory_default(capsys, schema_file):
    code, text, err = run(capsys, "generate", schema_file, "--scale-mode", "local_mp")
    assert code == 0 and "4,340 rows in 3 tables to memory" in text and "job local-" in text
    assert "into memory" in err


def test_dry_run_plans_chunks_and_writes_nothing(capsys, tmp_path, schema_file):
    code, text, _ = run(
        capsys, "generate", schema_file, "--scale-mode", "local_mp", "--chunk-size", 1000,
        "-o", tmp_path / "o", "--dry-run", "--json",
    )  # fmt: skip
    doc = json.loads(text)
    assert code == 0 and doc["chunks"] == {"customer": 1, "order": 2, "order_line": 4}
    assert not (tmp_path / "o").exists()


# ---- errors ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("extra", "text"),
    [
        (["--format", "csv"], "writes through sinks"),
        (["--chunk-rows", "5"], "--chunk-size"),
        (["--sink", "memory", "-o", "x"], "add --sink parquet"),
        (["--sink-config", "oops"], "SINK.KEY=VALUE"),
        (["--sink", "kql"], "needs cluster_uri"),
    ],
)
def test_bad_scale_arguments_exit_2(capsys, schema_file, extra, text):
    code, _, err = run(capsys, "generate", schema_file, "--scale-mode", "local_mp", *extra)
    assert code == 2 and text in err


def test_scale_mode_needs_a_target(capsys):
    code, _, err = run(capsys, "generate", "--scale-mode", "local_mp")
    assert code == 2 and "name a domain or a schema file" in err


def test_a_failing_sink_exits_1_and_names_the_job_to_resume(capsys, tmp_path, schema_file):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file")
    code, _, err = run(
        capsys, "generate", schema_file, "--scale-mode", "local_mp", "-o", blocker / "x"
    )
    assert code == 1 and "shape jobs resume local-" in err


def test_processes_with_a_second_sink_fail_clearly(capsys, tmp_path, schema_file):
    code, _, err = run(
        capsys, "generate", schema_file, "--scale-mode", "local_mp", "--processes", 2,
        "--sink", "parquet", "--sink", "memory", "-o", tmp_path / "o",
    )  # fmt: skip
    assert code == 1 and "parquet sink alone" in err


# ---- jobs: list, status, cancel, resume -----------------------------------------------------


def test_jobs_list_status_cancel_and_resume_a_local_job(capsys, tmp_path, schema_file, jobs_dir):
    out = tmp_path / "out"
    code, text, _ = run(
        capsys, "generate", schema_file, "--scale-mode", "local_mp", "-o", out, "--json"
    )
    job_id = json.loads(text)["job_id"]
    code, text, _ = run(capsys, "jobs", "list")
    assert code == 0 and job_id in text and "succeeded" in text
    code, text, _ = run(capsys, "jobs", "list", "--json")
    assert [j["job_id"] for j in json.loads(text)] == [job_id]
    code, text, _ = run(capsys, "jobs", "cancel", job_id, "--json")
    assert code == 0 and json.loads(text)["cancelled"] is False  # already final
    # a run that died: its record says failed; resume finishes it without redoing finished parts
    record = json.loads((jobs_dir / f"{job_id}.json").read_text())
    record.update(status="failed", error="killed")
    (jobs_dir / f"{job_id}.json").write_text(json.dumps(record))
    (out / "order_line" / "part-000000.parquet").unlink()
    code, text, _ = run(capsys, "jobs", "resume", job_id, "--json")
    doc = json.loads(text)
    assert code == 0 and doc["status"] == "succeeded" and doc["attempts"] == 2
    assert doc["result"]["parts_skipped"] == 2  # customer and order were whole
    assert part_rows(out, "order_line") == [3100]


def test_jobs_errors(capsys):
    code, _, err = run(capsys, "jobs", "status", "nope")
    assert code == 2 and "no job" in err
    code, text, _ = run(capsys, "jobs", "list")
    assert code == 0 and "no jobs" in text


def test_resume_of_a_succeeded_job_is_refused(capsys, schema_file):
    code, text, _ = run(capsys, "generate", schema_file, "--scale-mode", "local_mp", "--json")
    code, _, err = run(capsys, "jobs", "resume", json.loads(text)["job_id"])
    assert code == 1 and "only a failed or cancelled job resumes" in err


# ---- fabric_spark through the command line (recorded interactions) --------------------------


@pytest.fixture
def fabric(monkeypatch):
    fake = FakeFabric()
    monkeypatch.setattr("shape.scale.http.urllib_transport", fake)
    monkeypatch.setenv("SHAPE_FABRIC_TOKEN", "tok-1")
    monkeypatch.setenv("SHAPE_FABRIC_STORAGE_TOKEN", "stor-1")
    return fake


def spark_args(schema_file, *extra):
    return (
        "generate", schema_file, "--scale-mode", "fabric_spark", "--fabric-workspace", WS,
        "--fabric-lakehouse", LH, "--table-prefix", "t_", *extra,
    )  # fmt: skip


def test_fabric_spark_submit_status_cancel_and_resume(capsys, schema_file, fabric, jobs_dir):
    code, text, _ = run(capsys, *spark_args(schema_file, "--json"))
    doc = json.loads(text)
    assert code == 0 and doc["status"] == "submitted" and doc["fabric"]["notebook_item_id"] == NB
    assert doc["fabric"]["fabric_run_id"].startswith(RUN[:-1]) and doc["total_rows_queued"] == 4340
    job_id = doc["job_id"]
    (spec,) = fabric.uploaded_specs()
    assert spec["row_counts"] == ROWS and spec["table_prefix"] == "t_"

    fabric.job_status = "InProgress"
    code, text, _ = run(capsys, "jobs", "status", job_id, "--json")
    assert code == 0 and json.loads(text)["status"] == "running"
    code, text, _ = run(capsys, "jobs", "cancel", job_id, "--json")
    assert code == 0 and json.loads(text)["cancelled"] is True
    code, text, _ = run(capsys, "jobs", "resume", job_id, "--json")
    resumed = json.loads(text)
    assert code == 0 and resumed["attempts"] == 2 and resumed["status"] == "submitted"
    assert fabric.runs == 2 and len(fabric.uploaded_specs()) == 2
    assert "tok-1" not in "".join(p.read_text() for p in jobs_dir.iterdir())


def test_fabric_spark_human_output(capsys, schema_file, fabric):
    code, text, _ = run(capsys, *spark_args(schema_file))
    assert code == 0 and text.startswith("submitted spark-") and "shape jobs status" in text


def test_fabric_spark_needs_ids_and_a_token(capsys, schema_file, monkeypatch):
    monkeypatch.delenv("SHAPE_FABRIC_TOKEN", raising=False)
    code, _, err = run(capsys, "generate", schema_file, "--scale-mode", "fabric_spark")
    assert code == 2 and "workspace_id" in err


def test_jobs_status_of_a_spark_job_needs_the_token(capsys, schema_file, fabric, monkeypatch):
    code, text, _ = run(capsys, *spark_args(schema_file, "--json"))
    monkeypatch.delenv("SHAPE_FABRIC_TOKEN")
    code, _, err = run(capsys, "jobs", "status", json.loads(text)["job_id"])
    assert code == 2 and "SHAPE_FABRIC_TOKEN" in err


def test_an_unknown_target_or_scale_exits_2_and_makes_no_job(capsys, tmp_path, jobs_dir):
    code, _, err = run(capsys, "generate", tmp_path / "missing.json", "--scale-mode", "local_mp")
    assert code == 2 and "missing.json" in err
    code, _, err = run(
        capsys, "generate", "retail", "--scale", "nope", "--scale-mode", "local_single"
    )
    assert code == 2
    assert not jobs_dir.exists() or not list(jobs_dir.glob("*.json"))
