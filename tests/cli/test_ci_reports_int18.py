"""INT-18: the CI reports of W1-14 with the planned changes of W1-12 and the safe capture of W1-11.

A violation or change a planned change covers is a passing case that names the entry; one that
still fails (``severity: high``) stays failing; a rule a safe capture left without its value is
an error, never a pass."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from shape.cli import ci
from shape.cli.main import main

CONTRACT = {"columns": {"amt": {"min": 0}, "cat": {"allowed_values": ["x", "y"]}}}


def _by_name(checks: list[ci.Check]) -> dict[str, ci.Check]:
    return {c.name: c for c in checks}


def test_a_planned_violation_passes_and_names_its_entry() -> None:
    violations = [
        {"column": "amt", "rule": "min", "expected": 0, "observed": -1, "planned": {"id": "P-1"}},
        {"column": "cat", "rule": "allowed_values", "expected": ["x"], "observed": ["z"]},
    ]
    checks = _by_name(ci.checks_from_contract(CONTRACT, violations, "t", counted=[False, True]))
    assert checks["amt:min"].status == "pass" and checks["amt:min"].planned == "P-1"
    assert checks["cat:allowed_values"].status == "fail"
    assert checks["cat:allowed_values"].planned is None


def test_a_planned_violation_that_still_counts_fails() -> None:
    # `action: severity` raised to high: marked planned, and still a failure
    v = {"column": "amt", "rule": "min", "planned": {"id": "P-2", "action": "severity"}}
    checks = _by_name(ci.checks_from_contract(CONTRACT, [v], "t", counted=[True]))
    assert checks["amt:min"].status == "fail" and checks["amt:min"].planned == "P-2"


def test_without_counted_a_planned_mark_alone_decides() -> None:
    v = {"column": "amt", "rule": "min", "planned": {"id": "P-3"}}
    checks = _by_name(ci.checks_from_contract(CONTRACT, [v], "t"))
    assert checks["amt:min"].status == "pass"


def test_a_planned_change_in_a_diff_names_the_entry_id() -> None:
    change = {"column": "amt", "kind": "mean_shift", "severity": "high", "planned": {"id": "P-4"}}
    (check, *_) = ci.checks_from_diff([change], {"t": ["amt"]})
    assert check.status == "pass" and check.planned == "P-4"


def test_a_not_evaluable_rule_is_an_error_not_a_pass() -> None:
    gaps = [{"column": "cat", "rule": "allowed_values", "reason": "cat was captured safe"}]
    checks = _by_name(ci.checks_from_contract(CONTRACT, [], "t", not_evaluable=gaps))
    assert checks["cat:allowed_values"].status == "error"
    assert checks["amt:min"].status == "pass"
    assert sum(1 for c in checks.values() if c.check == "allowed_values") == 1


def test_check_of_a_safe_capture_reports_the_not_evaluable_rule(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rows = ["amt,cat"] + [f"{i},{'xy'[i % 2]}" for i in range(40)]
    Path("d.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    assert main(["profile", "d.csv", "-o", "p.shape", "--classify", "cat=CONFIDENTIAL"]) == 0
    # min and max need extremes a safe capture removes: a null-rate rule is evaluable
    contract = {"columns": {"amt": {"max_null_rate": 0.5}, "cat": CONTRACT["columns"]["cat"]}}
    Path("c.json").write_text(json.dumps(contract), encoding="utf-8")
    capsys.readouterr()
    assert main(["check", "p.shape", "c.json", "--junit", "r.xml"]) == 2  # W1-11: not evaluable
    assert "not evaluable" in capsys.readouterr().err
    suite = list(ET.parse("r.xml").getroot())[0]
    cases = {c.get("name"): c for c in suite.findall("testcase")}
    assert cases["cat:allowed_values"].find("error") is not None
    assert not list(cases["amt:max_null_rate"])  # passed
    assert int(suite.get("errors")) == 1 and int(suite.get("failures")) == 0
