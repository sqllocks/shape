"""CLI timings have no retired ratio gate and require authenticated equivalence first."""

import importlib.util
from pathlib import Path


def module():
    path = Path(__file__).resolve().parents[2] / "benchmarks/regression/profile_1to1/bench_cli.py"
    spec = importlib.util.spec_from_file_location("frz_profile_cli", path)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_missing_record_prevents_even_start_timing(monkeypatch):
    m = module()
    calls = []
    monkeypatch.setattr(m, "timed", lambda cmd: calls.append(cmd))
    monkeypatch.setattr(
        m,
        "equivalent",
        lambda *a: (_ for _ in ()).throw(m.RecordingError("profile:d1.csv: no recording")),
    )
    assert m.main(["d1.csv"]) == 2
    assert calls == []


def test_failed_equivalence_has_no_timing(monkeypatch, tmp_path):
    m = module()
    monkeypatch.setattr(m, "PROFILE_DATA_DIR", tmp_path)
    monkeypatch.setattr(m, "equivalent", lambda *a: ["mean mismatch"])
    monkeypatch.setattr(
        m, "timed", lambda *a: (_ for _ in ()).throw(AssertionError("timed a failure"))
    )
    result = m.prof_cli("d1.csv", 3)
    assert result["verifier"] == "fail"
    assert "shape_s" not in result and "baseline_s" not in result


def test_ratio_is_reported_without_retired_engine_gate(monkeypatch, tmp_path):
    m = module()
    monkeypatch.setattr(m, "PROFILE_DATA_DIR", tmp_path)
    monkeypatch.setattr(m, "equivalent", lambda *a, **kw: [])
    monkeypatch.setattr(m, "timed", lambda cmd: 2.0 if cmd[0] == m.BASELINE_SHAPE else 4.0)
    result = m.prof_cli("d1.csv", 3)
    assert result["verifier"] == "pass"
    assert result["ratio_vs_baseline"] == 0.5
    assert "gate" not in result
    assert m.START_GATE_MS == 300.0
