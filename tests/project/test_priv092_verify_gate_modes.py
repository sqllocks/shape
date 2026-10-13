"""PRIV-092 (old #591): ``gates.NAME.mode`` takes every gate ``shape verify`` runs, including the
four that are not in the built-in gate registry."""

from __future__ import annotations

import pytest

from shape.project import FORMAT, VERSION, parse_project, problems
from shape.quality.verify import _GATE_DESCRIPTIONS


def doc(gates: dict) -> dict:
    return {
        "format": FORMAT,
        "version": VERSION,
        "sources": {"t": {"path": "data"}},
        "gates": gates,
    }


VERIFY_ONLY = ["reconciliation", "timeseries_quality", "memorization", "utility"]


@pytest.mark.parametrize("gate", VERIFY_ONLY)
def test_a_verify_only_gate_takes_a_mode(gate, tmp_path):
    d = doc({gate: {"mode": "observe"}})
    assert problems(d) == []
    path = tmp_path / "shape.yml"
    path.write_text(
        f"format: shape-project\nversion: 1\nsources:\n  t: {{path: data}}\n"
        f"gates:\n  {gate}: {{mode: observe}}\n",
        encoding="utf-8",
    )
    project = parse_project(path.read_text(encoding="utf-8"), path)
    assert project.gate_mode(gate) == "observe"


def test_every_gate_verify_reports_is_accepted():
    assert problems(doc({g: {"mode": "observe"} for g in _GATE_DESCRIPTIONS})) == []


def test_fail_on_still_goes_with_baseline_gates_only():
    found = problems(doc({"reconciliation": {"mode": "enforce", "fail_on": "breaking"}}))
    assert any("gates.reconciliation.fail_on" in line for line in found), found


def test_an_unknown_gate_is_still_refused_and_the_list_names_the_new_gates():
    found = problems(doc({"nope": {"mode": "observe"}}))
    assert any("unknown gate 'nope'" in line and "reconciliation" in line for line in found)
