"""Behaviour of the comparison driver ``benchmarks/vs_spindle/run.py`` that needs no baseline
checkout: the workload steps are replaced by stand-ins, so only the driver's own logic runs."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmarks" / "vs_spindle"
sys.path.insert(0, str(BENCH))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


run = _load("vs_spindle_run_driver", BENCH / "run.py")


def _record(kind: str, **extra) -> dict:
    ver = {"status": "pass", "exit_code": 0, "command": "verify.py"}
    return {"kind": kind, "median_s": 1.0, "runs_s": [1.0], "verifier": ver, **extra}


def _offline(monkeypatch, tmp_path: Path) -> None:
    """Point the driver at existing stand-in paths and stub every subprocess step."""
    fake = tmp_path / "fake"
    (fake / "sqllocks_spindle").mkdir(parents=True)
    (fake / "python").write_text("")
    monkeypatch.setattr(run, "SPINDLE_PY", fake / "python")
    monkeypatch.setattr(run, "SHAPE_PY", fake / "python")
    monkeypatch.setattr(run, "SPINDLE_ROOT", fake)
    monkeypatch.setattr(run, "py_version", lambda py, pkg: "0")
    monkeypatch.setattr(run, "git_rev", lambda path: "abc")
    monkeypatch.setattr(run, "git_dirty", lambda path: False)
    monkeypatch.setenv("BENCH_LOCK_HELD", "1")

    def stream(scales, runs, out):
        for s in scales:
            out["spindle"]["workloads"][f"stream:retail:order:{s}"] = _record("stream")
            out["shape"]["workloads"][f"stream:retail:order:{s}"] = _record(
                "stream", speedup_vs_spindle=2.0
            )
        return True

    monkeypatch.setattr(run, "stream_workloads", stream)


def test_only_one_family_keeps_the_other_families_already_recorded(monkeypatch, tmp_path):
    """``--only stream`` re-measures streaming; the committed profiling and generation records
    (the section 6.2(4) reference) must survive, not be dropped from the file."""
    _offline(monkeypatch, tmp_path)
    out = tmp_path / "results.json"
    previous = {
        "schema_version": 1,
        "mode": "quick",
        "runs": 3,
        "generated_utc": "2026-01-01T00:00:00Z",
        "machine": {"cores": 4},
        "spindle_commit": "x",
        "shape_commit": "y",
        "shape_tree_dirty": False,
        "spindle": {
            "workloads": {
                "profile:d1.csv": _record("profile"),
                "generate:retail:small": _record("generate"),
            }
        },
        "reference_port": {
            "workloads": {
                "profile:d1.csv": _record("profile", speedup_vs_spindle=5.0),
                "generate:retail:small": _record("generate", speedup_vs_spindle=2.0),
            }
        },
        "shape": {"workloads": {"profile:d1.csv": _record("profile", speedup_vs_spindle=6.0)}},
    }
    out.write_text(json.dumps(previous))
    assert run.main(["--quick", "--only", "stream", "--out", str(out)]) == 0
    doc = json.loads(out.read_text())
    assert set(doc["spindle"]["workloads"]) == {
        "profile:d1.csv",
        "generate:retail:small",
        "stream:retail:order:small",
        "stream:retail:order:medium",
    }
    assert set(doc["reference_port"]["workloads"]) == {"profile:d1.csv", "generate:retail:small"}
    assert set(doc["shape"]["workloads"]) == {
        "profile:d1.csv",
        "stream:retail:order:small",
        "stream:retail:order:medium",
    }
    assert run.validate(doc, json.loads(run.SCHEMA_FILE.read_text())) == []


def test_a_failing_timed_baseline_run_leaves_the_record_empty(monkeypatch, tmp_path, capsys):
    """--full times every other domain on the baseline. A timed run that fails (after the first
    one passed) must leave that domain's record without numbers and say so, as a failing first
    run does, not stop the whole job with a bare StopIteration."""
    _offline(monkeypatch, tmp_path)

    class Dumped:
        returncode = 0
        stdout = "schemas/hr_3nf.json\n"
        stderr = ""

    monkeypatch.setattr(run.subprocess, "run", lambda *a, **k: Dumped())
    calls = iter([(0, 'GEN_JSON {"total_s": 1.0}\n'), (1, "Traceback: boom\n")])
    monkeypatch.setattr(run, "sh", lambda cmd, log=None: next(calls))
    monkeypatch.setattr(run, "wait_for_quiet", lambda *a, **k: 0.0, raising=False)
    out = {"spindle": {"workloads": {}}, "reference_port": {"workloads": {}}}
    run.other_domain_baselines(3, out)
    rec = out["spindle"]["workloads"]["generate:hr:medium"]
    assert rec["median_s"] is None and rec["runs_s"] == []
    assert "generate:hr:medium" in capsys.readouterr().err
