"""FRZ-01 recorder contracts use only stub workers and temporary stores."""

from __future__ import annotations

import json
import sys
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
    fake = tmp_path / "python"
    fake.write_text(
        "#!" + sys.executable + "\n"
        "import json,os,sys\n"
        "assert 'PYTHONPATH' not in os.environ and 'PYTHONHOME' not in os.environ\n"
        "if '-c' in sys.argv: print('0.9.1')\n"
        "else: print(json.dumps({'profile': {'columns': {}}, 'inputs': {}}))\n"
    )
    fake.chmod(0o755)
    monkeypatch.setattr(r, "BASELINE_PY", fake)
    monkeypatch.setenv("PYTHONPATH", "must-not-reach-the-release")
    freeze = tmp_path / "freeze"
    freeze.write_text("stub freeze\n")
    monkeypatch.setattr(r, "FREEZE", freeze)
    out = tmp_path / "recorded"
    assert r.main(["--profile", "--out", str(out)]) == 0
    from recorded_store import check_all, load

    assert check_all(out) == []
    assert len(json.loads((out / "MANIFEST.json").read_text())["files"]) == 49
    assert load("profile", "d1.csv", root=out)["profile"] == {"columns": {}}


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
