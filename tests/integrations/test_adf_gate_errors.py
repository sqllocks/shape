"""The ADF Batch gate scripts: an unexpected error is an error gate (exit 2), never exit 1.

Exit 1 means "contract violation" to the pipeline, so a crash must not produce it, and the gate
document must still be written for the Lookup activity (issue #366).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BATCH = REPO / "integrations" / "adf" / "batch"


def _load(name: str):
    sys.path.insert(0, str(BATCH))
    try:
        spec = importlib.util.spec_from_file_location(f"aud_{name}", BATCH / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(BATCH))


run_gate = _load("run_gate")
run_generate_gate = _load("run_generate_gate")


def test_a_cli_that_writes_nothing_is_an_error_gate(tmp_path):
    out = tmp_path / "out"
    settings = {"sourceUrl": str(tmp_path / "x.csv"), "outputUrl": str(out)}
    code = run_gate.run(settings, [sys.executable, "-c", "pass"], workdir=tmp_path)
    gate = json.loads((out / "gate.json").read_text())
    assert code == 2 and gate["exitCode"] == 2 and gate["passed"] is False
    assert "summary.json" in gate["error"]


def test_a_missing_shape_command_is_an_error_gate(tmp_path):
    out = tmp_path / "out"
    settings = {"sourceUrl": str(tmp_path / "x.csv"), "outputUrl": str(out)}
    code = run_gate.run(settings, ["no-such-shape-command-aud"], workdir=tmp_path)
    gate = json.loads((out / "gate.json").read_text())
    assert code == 2 and "no-such-shape-command-aud" in gate["error"]


def test_main_exits_2_for_a_malformed_activity_file(tmp_path):
    path = tmp_path / "activity.json"
    path.write_text(json.dumps({"typeProperties": "not an object"}))
    assert run_gate.main(["--activity", str(path)]) == 2


def test_an_unexpected_generation_error_is_an_error_gate(tmp_path, monkeypatch):
    from shape.integrations.fabric import generation

    def boom(*args, **kwargs):
        raise RuntimeError("planner exploded")

    monkeypatch.setattr(generation, "plan_row_counts", boom)
    out = tmp_path / "out"
    code = run_generate_gate.run(
        {"domain": "retail", "outputUrl": str(out)}, ["shape"], workdir=tmp_path
    )
    gate = json.loads((out / "gate.json").read_text())
    assert code == 2 and gate["exitCode"] == 2 and gate["domain"] == "retail"
    assert gate["error"] == "RuntimeError: planner exploded"


@pytest.mark.parametrize("script", [run_gate, run_generate_gate])
def test_main_exits_2_when_run_crashes(script, tmp_path, monkeypatch):
    path = tmp_path / "activity.json"
    path.write_text(json.dumps({"outputUrl": str(tmp_path / "out")}))

    def boom(*args, **kwargs):
        raise RuntimeError("upload exploded")

    monkeypatch.setattr(script, "run", boom)
    assert script.main(["--activity", str(path)]) == 2
