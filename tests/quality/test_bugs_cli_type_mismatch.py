"""BUGS-cli-1 #152: a value of the wrong type is a violation of a numeric rule, not a crash."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shape.cli.main import main
from shape.quality import validate_rows
from shape.quality.policy import Rule


def _rule(kind, value, field="amount"):
    return Rule(field=field, kind=kind, value=value, severity="error")


@pytest.mark.parametrize("kind", ["min", "max"])
@pytest.mark.parametrize("bad", ["abc", "", b"x", [1], {"a": 1}, object()])
def test_wrong_type_is_a_violation_not_a_crash(kind, bad):
    result = validate_rows([{"amount": bad}], (_rule(kind, 10.0),))
    assert not result.passed
    assert [(v.row, v.field, v.rule) for v in result.violations] == [(0, "amount", kind)]


@pytest.mark.parametrize("kind", ["min", "max"])
def test_numbers_and_none_are_judged_as_before(kind):
    rows = [{"amount": 10.0}, {"amount": None}, {"amount": 5}, {"amount": 15}, {}]
    result = validate_rows(rows, (_rule(kind, 10.0),))
    expected = [2] if kind == "min" else [3]
    assert [v.row for v in result.violations] == expected


def test_other_rows_are_still_checked_after_a_bad_one():
    rows = [{"amount": "abc"}, {"amount": 1.0}, {"amount": 50.0}]
    result = validate_rows(rows, (_rule("min", 10.0), _rule("max", 20.0)))
    assert sorted((v.row, v.rule) for v in result.violations) == [
        (0, "max"),
        (0, "min"),
        (1, "min"),
        (2, "max"),
    ]


def test_cli_reports_the_row_like_any_other_out_of_range_value(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rows = "\n".join(f"{i},10001,NY,{i * 1.5}" for i in range(50))
    (tmp_path / "data.csv").write_text("id,postal_code,state,amount\n" + rows + "\n")
    (tmp_path / "bad.csv").write_text("id,postal_code,state,amount\n999,10001,NY,abc\n")
    assert main(["capture", "data.csv", "-o", "base.json"]) == 0
    capsys.readouterr()
    # Inferred rules are warnings, so the exit code is the one an out-of-range number gets.
    code = main(["quality", "bad.csv", "--reference", "base.json"])
    out = json.loads(capsys.readouterr().out)
    assert {(v["row"], v["field"], v["rule"]) for v in out["violations"]} >= {
        (0, "amount", "min"),
        (0, "amount", "max"),
        (0, "id", "max"),
    }
    assert code == (0 if out["passed"] else 2)


def test_cli_clean_data_still_exits_0(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rows = "\n".join(f"{i},10001,NY,{i * 1.5}" for i in range(50))
    (tmp_path / "data.csv").write_text("id,postal_code,state,amount\n" + rows + "\n")
    assert main(["capture", "data.csv", "-o", "base.json"]) == 0
    capsys.readouterr()
    assert main(["quality", "data.csv", "--reference", "base.json"]) == 0
