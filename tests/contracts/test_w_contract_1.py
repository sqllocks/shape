"""W-contract-1: the CLI validates the same v1 rules as the evaluator."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pyarrow as pa
import pytest

import shape
from shape.cli.main import main
from shape.contracts.v1 import _COLUMN_RULES, _CONTRACT_KEYS

VALID = {
    "row_count": {"min": 1000, "max": 5000},
    "columns": {
        "order_id": {"dtype": "integer", "nullable": False, "unique": True},
        "status": {"nullable": False, "allowed_values": ["cancelled", "completed"]},
        "order_total": {"dtype": "float", "nullable": False, "min": 0},
    },
    "required_columns": ["order_id", "status", "order_total"],
    "allow_extra_columns": True,
}


@pytest.mark.parametrize(
    "contract",
    [VALID, {}, {"tables": {"orders": VALID}}, {"format": "shape-contract", "version": 1}],
)
def test_validate_accepts_v1_contracts(tmp_path, capsys, contract):
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract))
    assert main(["validate", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True


@pytest.fixture
def profile_path(tmp_path):
    path = tmp_path / "orders.shape"
    shape.save(shape.profile(pa.table({"order_id": [1, 2]})), path)
    return path


@pytest.mark.parametrize("command", ["validate", "check", "dry-run"])
@pytest.mark.parametrize(
    "rules",
    [
        {"not_null": True},
        {"nullabel": False},
        {"nullable": "false"},
        {"unique": 1},
        {"max_null_rate": "0.1"},
        {"allowed_values": "a"},
        {"dtype": []},
        {"pattern": {}},
        {"distribution": False},
        {"valid_as": {"kind": []}},
    ],
)
def test_malformed_column_rules_are_input_errors(tmp_path, capsys, profile_path, command, rules):
    path = tmp_path / "contract.json"
    path.write_text(json.dumps({"columns": {"order_id": rules}}))
    report = tmp_path / "report.json"
    args = (
        ["validate", str(path)]
        if command == "validate"
        else [
            "check",
            str(profile_path),
            str(path),
            "--json",
            str(report),
        ]
    )
    if command == "dry-run":
        args.append("--dry-run")
    assert main(args) == 2
    captured = capsys.readouterr()
    message = captured.out + captured.err
    assert "order_id" in message
    assert next(iter(rules)) in message
    if "nullabel" in rules:
        assert "did you mean 'nullable'" in message
    assert not report.exists()


@pytest.mark.parametrize(
    "contract", [{"columns": []}, {"row_count": []}, {"tables": {"orders": 5}}]
)
def test_validate_reports_structural_contract_errors(tmp_path, capsys, contract):
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract))
    assert main(["validate", str(path)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["valid"] is False
    assert result["errors"]


def test_valid_dry_run_checks_structure_without_evaluating_rules(tmp_path, capsys, profile_path):
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(VALID))
    assert main(["check", str(profile_path), str(path), "--dry-run"]) == 0
    assert "nothing would be written" in capsys.readouterr().out


def test_documented_column_rules_match_registry():
    docs = Path(__file__).resolve().parents[2] / "docs" / "CONTRACTS.md"
    row = next(line for line in docs.read_text().splitlines() if line.startswith("| `columns` |"))
    documented = set(re.findall(r"`([^`]+)`", row)) - {"columns"}
    assert documented == _COLUMN_RULES


@pytest.mark.parametrize(
    "contract",
    [
        {"row_count": {"min": "1000"}},
        {"allow_extra_columns": "true"},
        {"required_columns": "order_id"},
        {"tables": {"orders": {"columns": {"order_id": {"not_null": True}}}}},
    ],
)
def test_validate_rejects_malformed_table_rule_values(tmp_path, capsys, contract):
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract))
    assert main(["validate", str(path)]) == 2
    assert "error" in capsys.readouterr().err


def test_null_pattern_and_distribution_keep_their_existing_meaning(tmp_path, capsys):
    profile = shape.profile(pa.table({"order_id": [1, 2]}))
    contract = {"columns": {"order_id": {"pattern": None, "distribution": None}}}
    assert shape.check(profile, contract).passed
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract))
    assert main(["validate", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["valid"]


def test_check_dry_run_keeps_evidence_contract_vocabulary(tmp_path, capsys):
    data = tmp_path / "data.csv"
    data.write_text("id\n1\n2\n")
    artifact = tmp_path / "model.shape"
    assert main(["capture", str(data), "-o", str(artifact)]) == 0
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({"columns": {"id": {"kind": "numeric"}}}))
    capsys.readouterr()
    assert main(["check", str(artifact), str(contract), "--dry-run"]) == 0


def test_dry_run_validates_the_project_contract(tmp_path, capsys, monkeypatch):
    artifact = tmp_path / "orders.shape"
    shape.save(shape.profile(pa.table({"order_id": [1, 2]}), name="orders"), artifact)
    (tmp_path / "contract.json").write_text(
        json.dumps({"columns": {"order_id": {"not_null": True}}})
    )
    (tmp_path / "shape.yml").write_text(
        "format: shape-project\nversion: 1\nsources:\n"
        "  orders:\n    path: data.csv\n    contract: contract.json\n"
    )
    monkeypatch.chdir(tmp_path)
    assert main(["check", str(artifact), "--dry-run"]) == 2
    assert "not_null" in capsys.readouterr().err


def test_documented_contract_keys_match_registry():
    docs = Path(__file__).resolve().parents[2] / "docs" / "CONTRACTS.md"
    table = docs.read_text().split("Exit codes of", 1)[0]
    documented = {
        key
        for line in table.splitlines()
        if line.startswith("|")
        for key in re.findall(r"`([^`]+)`", line.split("|")[1])
    }
    assert documented == _CONTRACT_KEYS


@pytest.mark.parametrize("rule", ["timeseries", "reconcile"])
def test_data_rule_values_must_have_their_declared_type(tmp_path, capsys, rule):
    path = tmp_path / "contract.json"
    path.write_text(json.dumps({rule: True}))
    assert main(["validate", str(path)]) == 2
    assert rule in capsys.readouterr().err
