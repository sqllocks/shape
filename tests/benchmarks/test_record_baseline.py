"""FRZ-01 recorder contracts use only stub workers and temporary stores."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import venv
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[2] / "benchmarks" / "vs_refengine"
sys.path.insert(0, str(BENCH))


def recorder():
    import record_baseline

    return record_baseline


def stub(monkeypatch, tmp_path):
    r = recorder()
    monkeypatch.setattr(r, "check_release", lambda: ("0.9.1", "a" * 40))
    freeze = tmp_path / "baseline_freeze.txt"
    freeze.write_text("sqllocks-shape==0.9.1\n")
    monkeypatch.setattr(r, "FREEZE", freeze)

    def collect(kind, identifier, **kw):
        if kind == "timings":
            return {
                "schema_version": 2,
                "baseline": {"version": "0.9.1", "commit": "a" * 40, "workloads": {}},
                "shape": {"workloads": {}},
            }
        if kind == "profile":
            return {"profile": {"columns": {}}, "inputs": {"stub": "b" * 64}}
        if kind == "db":
            return {
                "profile": {"tables": {}, "relationships": []},
                "table_digests": {},
                "inputs": {},
            }
        if kind == "generate":
            return {
                "seeds": {
                    str(s): {"tables": {}, "dataset_id": "b" * 64} for s in (42, 43, 44, 45, 46)
                }
            }
        if kind == "stream":
            return {"seeds": {str(s): {"whole": {}, "prefix": {}} for s in (42, 43, 44, 45, 46)}}
        return {"report": {"exit_code": 0, "gates": []}, "inputs": {}}

    monkeypatch.setattr(r, "collect", collect)
    return r


def test_all_stub_records_manifest_and_checker(monkeypatch, tmp_path):
    r = stub(monkeypatch, tmp_path)
    out = tmp_path / "store"
    assert r.main(["--all", "--mssql", "stub-server", "--out", str(out)]) == 0
    doc = json.loads((out / "MANIFEST.json").read_text())
    assert set(doc["workloads"]) == set(r.workloads())
    assert doc["source_release"] == {"version": "0.9.1", "commit": "a" * 40}
    from recorded_store import check_all

    assert check_all(out) == []
    assert r.main(["--all", "--mssql", "stub-server", "--out", str(out)]) == 2
    assert r.main(["--all", "--mssql", "stub-server", "--replace", "--out", str(out)]) == 0


@pytest.mark.parametrize(
    "fault", ["wrong_version", "missing_tag", "missing_mssql", "failed_worker"]
)
def test_recorder_refuses_untrustworthy_or_incomplete_record(monkeypatch, tmp_path, fault, capsys):
    r = stub(monkeypatch, tmp_path)
    if fault == "wrong_version":
        monkeypatch.setattr(r, "check_release", lambda: ("0.9.2", "a" * 40))
    if fault == "missing_tag":
        monkeypatch.setattr(r, "check_release", lambda: ("0.9.1", ""))
    if fault == "failed_worker":
        monkeypatch.setattr(
            r, "collect", lambda *a, **kw: (_ for _ in ()).throw(ValueError("stub worker failed"))
        )
    out = tmp_path / "store"
    args = ["--all", "--out", str(out)]
    if fault != "missing_mssql":
        args += ["--mssql", "stub-server"]
    assert r.main(args) == 2
    assert not (out / "MANIFEST.json").exists()
    if fault == "wrong_version":
        assert "0.9.2" in capsys.readouterr().err


def test_recorder_runs_a_tiny_baseline_interpreter_end_to_end(monkeypatch, tmp_path):
    """No product import or real baseline capture: the subprocess is an explicit stand-in."""
    r = recorder()
    baseline = tmp_path / "baseline"
    venv.EnvBuilder(with_pip=False).create(baseline)
    fake = baseline / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    purelib = Path(
        subprocess.run(
            [str(fake), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    package = purelib / "shape"
    package.mkdir()
    guard = "assert 'PYTHONPATH' not in os.environ and 'PYTHONHOME' not in os.environ\n"
    (package / "__init__.py").write_text("import os\n" + guard + "__version__ = '0.9.1'\n")
    program = tmp_path / "stub_worker.py"
    program.write_text(
        "import json,os\n"
        + guard
        + "print(json.dumps({'profile': {'columns': {}}, 'inputs': {}}))\n"
    )
    monkeypatch.setattr(r, "BASELINE_PY", fake)
    monkeypatch.setattr(r, "__file__", str(program))
    monkeypatch.setenv("PYTHONPATH", "must-not-reach-the-release")
    monkeypatch.setenv("PYTHONHOME", "must-not-reach-the-release")
    freeze = tmp_path / "freeze"
    freeze.write_text("stub freeze\n")
    monkeypatch.setattr(r, "FREEZE", freeze)
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--allow-empty",
            "-m",
            "offline release",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "-C", str(source), "tag", "v0.9.1"], check=True)
    monkeypatch.setattr(r, "SHAPE_ROOT", source)
    out = tmp_path / "recorded"
    assert r.main(["--profile", "--out", str(out)]) == 0
    from recorded_store import check_all, load

    assert check_all(out) == []
    assert len(json.loads((out / "MANIFEST.json").read_text())["files"]) == 49
    assert load("profile", "d1.csv", root=out)["profile"] == {"columns": {}}

    subprocess.run(["git", "-C", str(source), "tag", "-d", "v0.9.1"], check=True)
    assert r.main(["--profile", "--out", str(tmp_path / "missing-tag")]) == 2
    assert not (tmp_path / "missing-tag").exists()


def test_real_recorder_refuses_a_checkout_without_the_release_tag(monkeypatch, tmp_path, capsys):
    r = recorder()
    source = tmp_path / "shallow-source"
    source.mkdir()
    subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
    monkeypatch.setattr(r, "SHAPE_ROOT", source)
    run = subprocess.run

    def release_version_or_real_git(command, **kwargs):
        if command[0] == str(r.BASELINE_PY):
            return subprocess.CompletedProcess(command, 0, "0.9.1\n", "")
        return run(command, **kwargs)

    monkeypatch.setattr(r.subprocess, "run", release_version_or_real_git)
    out = tmp_path / "recorded"
    assert r.main(["--profile", "--out", str(out)]) == 2
    assert "missing tag v0.9.1" in capsys.readouterr().err
    assert not out.exists()


def test_composite_recorder_uses_only_per_table_planned_counts(monkeypatch):
    from types import SimpleNamespace

    from shape.generation import engine

    r = recorder()
    calls = []

    def fake_engine(schema, scale, seed):
        calls.append((schema, scale, seed))
        return SimpleNamespace(
            row_counts={**{table: 7 for table in schema.tables}, "working_value": 99}
        )

    monkeypatch.setattr(engine, "Engine", fake_engine)
    result = r.plan_worker()
    assert len(result["composites"]) == 9
    assert calls and all(seed == 42 for _, _, seed in calls)
    for key, entry in result["composites"].items():
        assert entry["schema_sha256"] == r.sha256(r.HERE / "fixtures/composites" / (key + ".json"))
        assert all(
            "working_value" not in counts and set(counts.values()) == {7}
            for counts in entry["planned"].values()
        )


def test_release_only_timing_also_runs_verifiers_with_the_release(monkeypatch, tmp_path):
    from types import SimpleNamespace

    import paths

    r = recorder()
    baseline = tmp_path / "release/bin/python"
    monkeypatch.setattr(r, "BASELINE_PY", baseline)
    monkeypatch.setattr(r, "BENCH_OUT_DIR", tmp_path)
    seen = []

    def fake_main(args):
        seen.append(paths.SHAPE_PY)
        Path(args[args.index("--out") + 1]).write_text('{"schema_version": 2}')
        return 0

    monkeypatch.setitem(sys.modules, "run", SimpleNamespace(main=fake_main))
    assert r.timings_worker()["schema_version"] == 2
    assert seen == [baseline]


def test_failed_worker_reports_redacted_command_exit_and_tail(monkeypatch, capsys):
    import subprocess

    r = recorder()
    secret = "never-print-this-password"
    connection = "Driver=fake;UID=sa;PWD={" + secret + "};Database=tempdb"
    monkeypatch.setenv("BASELINE_MSSQL", connection)
    monkeypatch.setattr(
        r.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            a[0], 19, "\n".join(f"line{i}" for i in range(240)), "PWD=" + secret
        ),
    )
    with pytest.raises(r.RecordingError, match="db:name_only:mssql"):
        r.collect("db", "name_only:mssql", mssql=connection)
    error = capsys.readouterr().err
    assert "db:name_only:mssql" in error and "exit 19" in error
    assert "--worker db" in error and "--identifier name_only:mssql" in error
    assert "line239" in error and "line0\n" not in error
    assert secret not in error and "<redacted>" in error
    assert secret not in r.redacted_command(["record_baseline.py", "--mssql", connection])


def test_worker_failure_keeps_captured_output_and_exception(monkeypatch, capsys):
    from types import SimpleNamespace

    r = recorder()
    monkeypatch.setitem(sys.modules, "shape", SimpleNamespace(__version__=r.BASELINE_VERSION))

    def fail(identifier):
        print("profile subprocess: diagnostic before failure")
        raise RuntimeError("actionable baseline failure")

    monkeypatch.setattr(r, "profile_worker", fail)
    assert r.worker("profile", "d1.csv", None) == 2
    err = capsys.readouterr().err
    assert "profile:d1.csv" in err
    assert "diagnostic before failure" in err
    assert "RuntimeError: actionable baseline failure" in err


def test_smoke_starts_each_worker_with_small_inputs_without_publishing(monkeypatch, tmp_path):
    r = stub(monkeypatch, tmp_path)
    real_collect = r.collect
    calls = []

    def collect(kind, identifier, **kw):
        calls.append((kind, identifier, kw))
        return real_collect(kind, identifier, **kw)

    monkeypatch.setattr(r, "collect", collect)
    monkeypatch.setattr(r, "prepare_smoke_profile", lambda: None)
    out = tmp_path / "unpublished"
    assert r.main(["--all", "--smoke", "--mssql", "stub", "--out", str(out)]) == 0
    assert [(k, i) for k, i, _ in calls] == [
        ("profile", "edge/e3.csv"),
        ("profile", "edge/x_pq_list.parquet"),
        ("profile", "edge/x_pq_nested.parquet"),
        ("generate", "retail:small"),
        ("stream", "retail:order:small"),
        ("db", "name_only"),
        ("db", "name_only:mssql"),
        ("verify_cli", r.cli_cases()[0]),
        ("plan", "composites"),
        ("pools", "retail"),
        ("timings", "0.9.1"),
    ]
    assert calls[-1][2]["smoke"] is True
    assert not out.exists()
    assert not list(tmp_path.glob("shape-baseline-record-*"))


def test_smoke_preserves_existing_store_and_stops_on_first_failed_worker(monkeypatch, tmp_path):
    r = stub(monkeypatch, tmp_path)
    monkeypatch.setattr(r, "prepare_smoke_profile", lambda: None)
    out = tmp_path / "store"
    out.mkdir()
    (out / "sentinel").write_text("do not replace")
    seen = []

    def fail(kind, identifier, **kw):
        seen.append((kind, identifier))
        raise r.RecordingError("profile:edge/e3.csv: baseline worker failed (exit 19)")

    monkeypatch.setattr(r, "collect", fail)
    assert r.main(["--all", "--smoke", "--mssql", "stub", "--out", str(out)]) == 2
    assert seen == [("profile", "edge/e3.csv")]
    assert (out / "sentinel").read_text() == "do not replace"


def test_worker_launch_failure_names_command_and_workload(monkeypatch, capsys):
    r = recorder()

    def missing(*args, **kwargs):
        raise FileNotFoundError("baseline executable missing")

    monkeypatch.setattr(r.subprocess, "run", missing)
    with pytest.raises(r.RecordingError, match="profile:edge/e3.csv"):
        r.collect("profile", "edge/e3.csv")
    error = capsys.readouterr().err
    assert "--worker profile" in error
    assert "exit 2" in error and "baseline executable missing" in error


def test_smoke_timing_keeps_release_interpreter_and_restores_full_plan(monkeypatch, tmp_path):
    from types import SimpleNamespace

    import paths

    r = recorder()
    baseline = tmp_path / "release/bin/python"
    monkeypatch.setattr(r, "BASELINE_PY", baseline)
    monkeypatch.setattr(r, "BENCH_OUT_DIR", tmp_path)
    seen = []
    fake = SimpleNamespace(
        QUICK_PROFILE_DATASETS=["original"],
        QUICK_DOMAINS=[("original", ["medium"])],
        QUICK_STREAM_SCALES=["medium"],
    )

    def fake_main(args):
        seen.append(
            (
                args,
                paths.SHAPE_PY,
                fake.QUICK_PROFILE_DATASETS,
                fake.QUICK_DOMAINS,
                fake.QUICK_STREAM_SCALES,
            )
        )
        Path(args[args.index("--out") + 1]).write_text('{"schema_version": 2}')
        return 0

    fake.main = fake_main
    monkeypatch.setitem(sys.modules, "run", fake)
    assert r.timings_worker(smoke=True)["schema_version"] == 2
    assert seen[0][0][:2] == ["--quick", "--baseline-only"]
    assert seen[0][1:] == (
        baseline,
        ["edge/e3.csv", "edge/x_pq_list.parquet", "edge/x_pq_nested.parquet"],
        [("retail", ["small"])],
        ["small"],
    )
    assert fake.QUICK_PROFILE_DATASETS == ["original"]
    assert fake.QUICK_DOMAINS == [("original", ["medium"])]
    assert fake.QUICK_STREAM_SCALES == ["medium"]


@pytest.mark.parametrize("password", ["simple", "part;two", "closing}brace"])
def test_diagnostic_redacts_braced_connection_and_unbraced_password(password):
    r = recorder()
    escaped = password.replace("}", "}}")
    connection = "Driver=fake;UID=test;PWD={" + escaped + "};Database=tempdb"
    message = connection + "\nlogin rejected: " + password
    redacted = r.redact(message, connection)
    assert password not in redacted and connection not in redacted
    assert "<redacted connection>" in redacted and "<redacted>" in redacted
