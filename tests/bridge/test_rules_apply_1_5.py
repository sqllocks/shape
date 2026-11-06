"""Rule application creates and extends contracts without discarding existing rules."""

from __future__ import annotations

import json

import pytest
from bridge_helpers import Caller

from shape.cli.main import main
from shape.proposals import DecisionFile, Proposal


class Caller15(Caller):
    version = "1.5"


@pytest.fixture
def api15(bridge):
    return Caller15(bridge)


@pytest.fixture
def decisions(tmp_path):
    file = DecisionFile.empty()
    proposals = [
        Proposal(
            f"rule:t.{name}.{rule}",
            "rule",
            f"t.{name}.{rule}",
            {"columns": {name: {rule: value}}},
            0.9,
        )
        for name, rule, value in [
            ("id", "unique", True),
            ("age", "nullable", False),
            ("code", "unique", True),
            ("value", "nullable", False),
        ]
    ]
    file.update(proposals)
    file.decide(proposals[0].id, "accepted", actor="tester")
    file.decide(proposals[1].id, "rejected", actor="tester")
    file.decide(proposals[2].id, "deferred", actor="tester")
    path = tmp_path / "decisions.json"
    file.write(path)
    return path


def test_create_counts_only_accepted_and_rejected(api15, decisions, tmp_path):
    contract = tmp_path / "contract.json"
    result = api15.ok("rules_apply", decisions=str(decisions), contract=str(contract))
    assert result == {"contract": str(contract), "applied": 1, "rejected": 1}
    assert json.loads(contract.read_text()) == {"columns": {"id": {"unique": True}}}


def test_preserves_existing_rules_and_rerun_bytes(api15, decisions, tmp_path):
    contract = tmp_path / "contract.json"
    existing = {"row_count": {"min": 2}, "columns": {"other": {"nullable": False}}}
    contract.write_text(json.dumps(existing))
    assert api15.ok("rules_apply", decisions=str(decisions), contract=str(contract))["applied"] == 1
    merged = json.loads(contract.read_text())
    assert merged["row_count"] == existing["row_count"]
    assert merged["columns"]["other"] == existing["columns"]["other"]
    before = contract.read_bytes()
    assert api15.ok("rules_apply", decisions=str(decisions), contract=str(contract))["applied"] == 0
    assert contract.read_bytes() == before
    assert api15.ok("contract_validate", path=str(contract))


def test_conflict_does_not_change_contract_or_decisions(api15, decisions, tmp_path):
    contract = tmp_path / "contract.json"
    contract.write_text('{"columns":{"id":{"unique":false}}}')
    before = contract.read_bytes(), decisions.read_bytes()
    api15.fail(
        "rules_apply", "input.contract_conflict", decisions=str(decisions), contract=str(contract)
    )
    assert (contract.read_bytes(), decisions.read_bytes()) == before


def test_no_accepted_rules_does_not_create_contract(api15, tmp_path):
    decisions = tmp_path / "d.json"
    DecisionFile.empty().write(decisions)
    contract = tmp_path / "c.json"
    assert api15.ok("rules_apply", decisions=str(decisions), contract=str(contract))["applied"] == 0
    assert not contract.exists()


@pytest.mark.parametrize("content", ["{", "[]", '{"unknown":true}'])
def test_bad_contract_refused_without_write(api15, decisions, tmp_path, content):
    contract = tmp_path / "c.json"
    contract.write_text(content)
    reply = api15.call("rules_apply", decisions=str(decisions), contract=str(contract))
    assert not reply["ok"] and reply["error"]["code"].startswith("input.")
    assert contract.read_text() == content


def test_invalid_decisions_and_missing_file(api15, tmp_path):
    decisions, contract = tmp_path / "d.json", tmp_path / "c.json"
    api15.fail("rules_apply", "input.not_found", decisions=str(decisions), contract=str(contract))
    decisions.write_text('{"format":"shape-decisions","version":3}')
    api15.fail(
        "rules_apply",
        "input.unsupported_format_version",
        decisions=str(decisions),
        contract=str(contract),
    )
    decisions.write_text("[]")
    api15.fail(
        "rules_apply", "input.invalid_schema", decisions=str(decisions), contract=str(contract)
    )
    assert not contract.exists()


def test_same_file_refused(api15, decisions):
    before = decisions.read_bytes()
    api15.fail(
        "rules_apply", "usage.invalid_argument", decisions=str(decisions), contract=str(decisions)
    )
    assert decisions.read_bytes() == before


def test_write_failure(api15, decisions, tmp_path):
    contract = tmp_path / "missing" / "c.json"
    api15.fail("rules_apply", "io.write_failed", decisions=str(decisions), contract=str(contract))
    assert not contract.exists()


def test_older_bridge_cannot_apply(api12, decisions, tmp_path):
    api12.fail(
        "rules_apply",
        "usage.unknown_command",
        decisions=str(decisions),
        contract=str(tmp_path / "c.json"),
    )


def test_cli_matches_bridge(api15, decisions, tmp_path, capsys):
    target = tmp_path / "cli.json"
    assert main(["rules", "apply", str(target), "--decisions", str(decisions), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert {key: result[key] for key in ("contract", "applied", "rejected")} == {
        "contract": str(target),
        "applied": 1,
        "rejected": 1,
    }
    other = tmp_path / "bridge.json"
    api15.ok("rules_apply", decisions=str(decisions), contract=str(other))
    assert target.read_bytes() == other.read_bytes()


@pytest.mark.parametrize(
    "claim", [{}, {"columns": {"id": {"unique": "yes"}}}, {"tables": {}}, {"unknown": 2}]
)
def test_invalid_rule_claim_is_refused(api15, decisions, tmp_path, claim):
    doc = json.loads(decisions.read_text())
    next(p for p in doc["proposals"] if p["id"] == "rule:t.id.unique")["claim"] = claim
    decisions.write_text(json.dumps(doc))
    contract = tmp_path / "c.json"
    reply = api15.call("rules_apply", decisions=str(decisions), contract=str(contract))
    assert not reply["ok"] and reply["error"]["code"].startswith("input.")
    assert not contract.exists()


def test_conflict_after_another_rule_still_writes_nothing(api15, decisions, tmp_path):
    file = DecisionFile.read(decisions)
    file.decide("rule:t.age.nullable", "accepted", actor="tester")
    file.write(decisions)
    contract = tmp_path / "c.json"
    contract.write_text('{"columns":{"id":{"unique":false}}}')
    before = contract.read_bytes()
    api15.fail(
        "rules_apply", "input.contract_conflict", decisions=str(decisions), contract=str(contract)
    )
    assert contract.read_bytes() == before
    assert "age" not in json.loads(contract.read_text())["columns"]


def test_stale_rule_is_not_applied(api15, decisions, tmp_path):
    doc = json.loads(decisions.read_text())
    next(d for d in doc["decisions"] if d["proposal"] == "rule:t.id.unique")["status"] = "stale"
    decisions.write_text(json.dumps(doc))
    contract = tmp_path / "c.json"
    assert api15.ok("rules_apply", decisions=str(decisions), contract=str(contract))["applied"] == 0
    assert not contract.exists()


def test_wrong_argument_type_and_unknown_argument(api15, decisions, tmp_path):
    api15.fail(
        "rules_apply", "usage.invalid_argument", decisions=[], contract=str(tmp_path / "c.json")
    )
    api15.fail(
        "rules_apply",
        "usage.unknown_argument",
        decisions=str(decisions),
        contract=str(tmp_path / "c.json"),
        force=True,
    )
    assert not (tmp_path / "c.json").exists()
