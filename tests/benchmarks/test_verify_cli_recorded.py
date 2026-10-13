"""Every gate scenario authenticates fresh release reports before candidate comparison."""

import copy
import importlib.util
from pathlib import Path

import pytest


def module():
    path = Path(__file__).resolve().parents[2] / "benchmarks/regression/verify_1to1/verify.py"
    spec = importlib.util.spec_from_file_location("frz_verify_cli", path)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


SCENARIOS = [row[0] for row in module().SCENARIOS]


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("damage", ["equal", "candidate", "fresh_release", "missing", "tamper"])
def test_each_verify_cli_scenario_authenticates_then_compares(
    monkeypatch, tmp_path, scenario, damage
):
    m = module()
    base = {
        "exit_code": 1,
        "report": {
            "passed": False,
            "row_counts": {"t": 7},
            "gates": [
                {
                    "gate": "schema_conformance",
                    "passed": False,
                    "errors": [],
                    "warnings": [],
                    "details": {},
                }
            ],
        },
    }
    calls = []

    def load(*a, **kw):
        if damage in ("missing", "tamper"):
            raise m.RecordingError(f"verify_cli:{scenario}: {damage} recording")
        return {"runs": {run: base for run in m.RUNS}}

    def cli(tool, *a):
        calls.append(tool)
        result = copy.deepcopy(base)
        if (
            tool == "shape"
            and damage == "candidate"
            or tool == "baseline"
            and damage == "fresh_release"
        ):
            result["report"]["gates"][0]["warnings"] = ["changed warning"]
        return result

    monkeypatch.setattr(m, "load", load)
    monkeypatch.setattr(m, "scenario_data", lambda *a: (tmp_path, tmp_path / "schema.json"))
    monkeypatch.setattr(m, "cli_report", cli)
    code = m.main([scenario])
    assert code == (
        2 if damage in ("fresh_release", "missing", "tamper") else int(damage == "candidate")
    )
    if damage == "fresh_release":
        assert calls == ["baseline"]
    if damage in ("missing", "tamper"):
        assert calls == []
    if damage in ("equal", "candidate"):
        assert calls == ["baseline", "shape", "baseline", "shape"]


def test_unknown_scenario_is_refused():
    with pytest.raises(SystemExit) as caught:
        module().main(["not-a-scenario"])
    assert caught.value.code == 2
