"""Behaviour of the comparison driver ``benchmarks/vs_refengine/run.py`` that needs no baseline
checkout: the workload steps are replaced by stand-ins, so only the driver's own logic runs."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmarks" / "vs_refengine"
sys.path.insert(0, str(BENCH))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


run = _load("vs_refengine_run_driver", BENCH / "run.py")


def _record(kind: str, **extra) -> dict:
    ver = {"status": "pass", "exit_code": 0, "command": "verify.py"}
    return {"kind": kind, "median_s": 1.0, "runs_s": [1.0], "verifier": ver, **extra}


def _offline(monkeypatch, tmp_path: Path) -> None:
    """Point the driver at existing stand-in paths and stub every subprocess step."""
    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / "python").write_text("")
    monkeypatch.setattr(run, "BASELINE_PY", fake / "python")
    monkeypatch.setattr(run, "SHAPE_PY", fake / "python")
    monkeypatch.setattr(
        run, "py_version", lambda py, pkg: "0.9.3" if pkg == "sqllocks-shape" else "0"
    )
    monkeypatch.setattr(run, "py_kernel", lambda py: "rust")
    monkeypatch.setattr(
        run, "recording_manifest", lambda: {"shape_version": "0.9.3", "shape_commit": "a" * 40}
    )
    monkeypatch.setattr(run, "git_rev", lambda path: "abc")
    monkeypatch.setattr(run, "git_dirty", lambda path: False)
    monkeypatch.setenv("BENCH_LOCK_HELD", "1")

    def stream(scales, runs, out):
        for s in scales:
            out["baseline"]["workloads"][f"stream:retail:order:{s}"] = _record("stream")
            out["shape"]["workloads"][f"stream:retail:order:{s}"] = _record(
                "stream", ratio_vs_baseline=2.0
            )
        return True

    monkeypatch.setattr(run, "stream_workloads", stream)


def test_only_one_family_keeps_the_other_families_already_recorded(monkeypatch, tmp_path):
    """``--only stream`` re-measures streaming; the committed profiling and generation records
    (the section 6.2(4) reference) must survive, not be dropped from the file."""
    _offline(monkeypatch, tmp_path)
    out = tmp_path / "results.json"
    previous = {
        "schema_version": 2,
        "mode": "quick",
        "runs": 3,
        "generated_utc": "2026-01-01T00:00:00Z",
        "machine": {"cores": 4},
        "shape_commit": "y",
        "shape_tree_dirty": False,
        "baseline": {
            "kind": "shape",
            "version": "0.9.3",
            "commit": "a" * 40,
            "workloads": {
                "profile:d1.csv": _record("profile"),
                "generate:retail:small": _record("generate"),
            },
        },
        "shape": {"workloads": {"profile:d1.csv": _record("profile", ratio_vs_baseline=6.0)}},
    }
    out.write_text(json.dumps(previous))
    assert run.main(["--quick", "--only", "stream", "--out", str(out)]) == 0
    doc = json.loads(out.read_text())
    assert set(doc["baseline"]["workloads"]) == {
        "profile:d1.csv",
        "generate:retail:small",
        "stream:retail:order:small",
        "stream:retail:order:medium",
    }
    assert set(doc["shape"]["workloads"]) == {
        "profile:d1.csv",
        "stream:retail:order:small",
        "stream:retail:order:medium",
    }
    assert run.validate(doc, json.loads(run.SCHEMA_FILE.read_text())) == []


def test_a_failing_timed_baseline_run_leaves_the_record_empty(monkeypatch, tmp_path, capsys):
    """--full times every baseline domain (both sides, in ``domain_workloads``). A timed run that
    fails (after the first verifier run passed) must leave that domain's record without numbers
    and say so, as a failing first run does, not stop the whole job (#359)."""
    _offline(monkeypatch, tmp_path)
    monkeypatch.setattr(run, "BENCH_OUT_DIR", tmp_path / "out")
    calls = iter([(0, "verifier passed\n"), (1, "Traceback: boom\n")])
    monkeypatch.setattr(run, "sh", lambda cmd, log=None: next(calls))
    out: dict = {"baseline": {"workloads": {}}, "shape": {"workloads": {}}}
    assert run.domain_workloads("hr", ["medium"], 3, out, "shape") is False
    rec = out["baseline"]["workloads"]["generate:hr:medium"]
    assert rec["median_s"] is None and rec["runs_s"] == []
    shape = out["shape"]["workloads"]["generate:hr:medium"]
    assert shape["median_s"] is None and shape["runs_s"] == []
    assert shape["ratio_vs_baseline"] is None
    assert "generate:hr:medium" in capsys.readouterr().err


def test_missing_recording_records_unavailable_and_never_times(monkeypatch, tmp_path):
    _offline(monkeypatch, tmp_path)
    from recorded_store import RecordingError

    monkeypatch.setattr(
        run, "recording_manifest", lambda: (_ for _ in ()).throw(RecordingError("no recording"))
    )
    monkeypatch.setattr(
        run, "profile_workloads", lambda *args: __import__("pytest").fail("must not time")
    )
    out = tmp_path / "missing.json"
    assert run.main(["--quick", "--out", str(out)]) == 2
    doc = json.loads(out.read_text())
    assert len(doc["shape"]["workloads"]) == 8
    assert all(
        r["verifier"]["status"] == "unavailable" and r["median_s"] is None
        for r in doc["shape"]["workloads"].values()
    )


def test_failed_timing_subprocess_retains_last_output_lines(monkeypatch, capsys):
    from types import SimpleNamespace

    lines = [f"worker-line-{i}\n" for i in range(650)]
    monkeypatch.setattr(
        run.subprocess,
        "Popen",
        lambda *a, **kw: SimpleNamespace(stdout=iter(lines), wait=lambda: 17),
    )
    rc, output = run.sh(["baseline-python", "profile-worker"])
    assert rc == 17 and output == "".join(lines)
    shown = capsys.readouterr().out
    assert "baseline-python profile-worker" in shown
    assert "exit 17" in shown
    assert "worker-line-649" in shown
    assert "worker-line-450" in shown


@pytest.mark.parametrize("family", ["profile", "generate", "stream"])
def test_release_recording_stops_at_first_failed_timing_worker(monkeypatch, family):
    monkeypatch.setattr(run, "BASELINE_ONLY", True)
    calls = []
    directory = {"profile": run.PROFILE, "generate": run.DOMAIN, "stream": run.STREAM}[family]

    def sh(command):
        calls.append(command)
        return (17, "actionable failure") if str(directory / "bench.py") in command else (0, "")

    monkeypatch.setattr(run, "sh", sh)
    out = {"baseline": {"workloads": {}}, "shape": {"workloads": {}}}
    with pytest.raises(run.RecordingError, match=family + ":.*exit 17"):
        if family == "profile":
            run.profile_workloads(["d1.csv", "d2.csv"], 5, out)
        elif family == "generate":
            run.domain_workloads("retail", ["small", "medium"], 5, out)
        else:
            run.stream_workloads(["small", "medium"], 5, out)
    assert len(calls) == (3 if family == "profile" else 2)
    assert calls[-1][-1] == "--baseline-only"
    if family == "profile":
        assert calls[-1][-2:] == ["d1.csv", "--baseline-only"]
