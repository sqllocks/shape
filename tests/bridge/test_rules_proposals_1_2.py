"""W7-05 item 3 (part): ``rule`` in the kinds of ``proposals_propose`` and ``proposals_contract``."""

from __future__ import annotations

import json

import pytest
from data_1_1 import shop_tables, write_dataset

from shape.cli.main import main

UNIQUE = "rule:customers.customer_id.unique"
DTYPE = "rule:orders.amount.dtype"
ROWS = "rule:orders.row_count"


@pytest.fixture
def shop(tmp_path, api12):
    data = write_dataset(tmp_path / "data", shop_tables())
    api12.ok("profile", source=str(data), dataset=True, output=str(tmp_path / "shop.shape"))
    return tmp_path


def cli(capsys, *argv):
    code = main(list(argv))
    return code, capsys.readouterr().out


def normal(text: str) -> dict:
    doc = json.loads(text)
    for p in doc["proposals"]:
        p["proposed_at"] = "T"
    for d in doc["decisions"]:
        d["at"] = "T"
    return doc


def propose_rules(api12, shop, name="d.json"):
    return api12.ok(
        "proposals_propose",
        profile=str(shop / "shop.shape"),
        decisions=str(shop / name),
        kinds=["rule"],
        data=[str(shop / "data")],
    )


# ---- proposals_propose: the kind `rule` ------------------------------------------------------


def test_rule_proposals_write_the_decision_file_the_cli_writes(shop, api12, capsys):
    result = propose_rules(api12, shop, "bridge.json")
    code, out = cli(
        capsys,
        "proposals",
        "propose",
        str(shop / "shop.shape"),
        "--decisions",
        str(shop / "cli.json"),
        "--kinds",
        "rule",
        "--data",
        str(shop / "data"),
    )
    assert code == 0
    cli_result = json.loads(out)
    assert normal((shop / "bridge.json").read_text()) == normal((shop / "cli.json").read_text())
    assert json.loads((shop / "bridge.json").read_text())["version"] == 2
    assert result["proposals"] == cli_result["proposals"] > 0
    assert result["added"] == cli_result["added"] and result["stale"] == cli_result["stale"] == []
    assert UNIQUE in result["proposal_ids"] and ROWS in result["proposal_ids"]


def test_the_default_kinds_do_not_include_rule(shop, api12):
    result = api12.ok(
        "proposals_propose", profile=str(shop / "shop.shape"), decisions=str(shop / "d.json")
    )
    assert not [i for i in result["proposal_ids"] if i.startswith("rule:")]
    assert "stale" not in result  # the shape of a run without rules is unchanged
    assert json.loads((shop / "d.json").read_text())["version"] == 1


def test_a_rule_run_keeps_the_other_kinds_decisions_and_lists_by_kind(shop, api12):
    api12.ok(
        "proposals_propose",
        profile=str(shop / "shop.shape"),
        decisions=str(shop / "d.json"),
        data=[str(shop / "data")],
    )
    propose_rules(api12, shop)
    listed = api12.ok("proposals_list", decisions=str(shop / "d.json"), kind="rule")
    assert listed["count"] > 0 and {p["kind"] for p in listed["proposals"]} == {"rule"}
    others = api12.ok("proposals_list", decisions=str(shop / "d.json"), kind="pii")
    assert others["count"] > 0  # not withdrawn by the rule run


def test_a_rule_can_be_decided_and_a_stale_status_is_a_filter(shop, api12):
    propose_rules(api12, shop)
    decided = api12.ok(
        "proposals_decide",
        decisions=str(shop / "d.json"),
        proposal=UNIQUE,
        verb="accept",
        actor="ana",
    )
    assert decided["kind"] == "rule" and decided["decision"]["status"] == "accepted"
    assert api12.ok("proposals_list", decisions=str(shop / "d.json"), status="stale")["count"] == 0


def test_a_rule_the_data_no_longer_supports_is_marked_stale(shop, api12, tmp_path):
    propose_rules(api12, shop)
    api12.ok("proposals_decide", decisions=str(shop / "d.json"), proposal=ROWS, verb="accept",
             actor="ana")  # fmt: skip
    small = write_dataset(tmp_path / "small", {"customers": shop_tables()["customers"].slice(0, 5)})
    api12.ok("profile", source=str(small), dataset=True, output=str(tmp_path / "small.shape"))
    again = api12.ok(
        "proposals_propose",
        profile=str(tmp_path / "small.shape"),
        decisions=str(shop / "d.json"),
        kinds=["rule"],
    )
    assert ROWS in again["stale"]
    stale = api12.ok("proposals_list", decisions=str(shop / "d.json"), status="stale")
    assert [p["id"] for p in stale["proposals"]] == [ROWS]


def test_rule_kinds_boundaries(shop, api12):
    api12.fail(
        "proposals_propose",
        "usage.invalid_argument",
        profile=str(shop / "shop.shape"),
        decisions=str(shop / "d.json"),
        kinds=["rule", "mystery"],
    )
    api12.fail("proposals_list", "usage.invalid_argument", decisions="x", status="done")
    api12.fail("proposals_list", "usage.invalid_argument", decisions="x", kind="rules")
    assert not (shop / "d.json").exists()


def test_a_1_1_request_reads_a_version_1_file_and_refuses_a_version_2_one(shop, api12, api11):
    api11.ok("proposals_propose", profile=str(shop / "shop.shape"), decisions=str(shop / "v1.json"))
    assert api11.ok("proposals_list", decisions=str(shop / "v1.json"))["count"] > 0
    propose_rules(api12, shop, "v2.json")
    error = api11.fail(
        "proposals_list", "input.unsupported_format_version", decisions=str(shop / "v2.json")
    )
    assert "version 2" in error["message"]
    assert api12.ok("proposals_list", decisions=str(shop / "v2.json"))["count"] > 0


def test_a_version_3_decision_file_is_refused_by_the_1_2_bridge(shop, api12):
    doc = {"format": "shape-decisions", "version": 3, "proposals": [], "decisions": []}
    (shop / "v3.json").write_text(json.dumps(doc))
    api12.fail(
        "proposals_list", "input.unsupported_format_version", decisions=str(shop / "v3.json")
    )


# ---- proposals_contract ----------------------------------------------------------------------


def accept(api12, shop, *ids):
    propose_rules(api12, shop)
    for rule in ids:
        api12.ok("proposals_decide", decisions=str(shop / "d.json"), proposal=rule, verb="accept",
                 actor="ana")  # fmt: skip


def test_the_contract_is_the_one_the_cli_writes(shop, api12, capsys):
    accept(api12, shop, UNIQUE, DTYPE, ROWS)
    result = api12.ok(
        "proposals_contract", decisions=str(shop / "d.json"), output=str(shop / "bridge.json")
    )
    code, out = cli(
        capsys,
        "proposals",
        "contract",
        "--decisions",
        str(shop / "d.json"),
        "-o",
        str(shop / "cli.json"),
    )
    assert code == 0
    assert (shop / "bridge.json").read_bytes() == (shop / "cli.json").read_bytes()
    assert result == {"written": str(shop / "bridge.json"), "rules": 3}
    assert json.loads(out) == {"written": str(shop / "cli.json"), "rules": 3}
    contract = json.loads((shop / "bridge.json").read_text())
    assert contract["tables"]["customers"]["columns"]["customer_id"]["unique"] is True


def test_the_contract_the_bridge_writes_is_one_check_reads(shop, api12):
    accept(api12, shop, UNIQUE, DTYPE)
    api12.ok("proposals_contract", decisions=str(shop / "d.json"), output=str(shop / "c.json"))
    assert api12.ok("contract_validate", path=str(shop / "c.json"))
    checked = api12.ok("check", profile=str(shop / "shop.shape"), contract=str(shop / "c.json"))
    assert checked["passed"] is True


def test_merge_adds_the_rules_to_an_existing_contract(shop, api12):
    accept(api12, shop, UNIQUE)
    existing = {"tables": {"orders": {"row_count": {"min": 1}}}, "drift": {"thresholds": {}}}
    (shop / "base.json").write_text(json.dumps(existing))
    result = api12.ok(
        "proposals_contract",
        decisions=str(shop / "d.json"),
        output=str(shop / "merged.json"),
        merge=str(shop / "base.json"),
    )
    merged = json.loads((shop / "merged.json").read_text())
    assert result["rules"] == 1 and merged["drift"] == existing["drift"]
    assert merged["tables"]["orders"] == {"row_count": {"min": 1}}
    assert merged["tables"]["customers"]["columns"]["customer_id"]["unique"] is True
    assert json.loads((shop / "base.json").read_text()) == existing  # left unchanged


def test_a_conflict_names_both_rules_and_writes_nothing(shop, api12, capsys):
    accept(api12, shop, ROWS)
    existing = {"tables": {"orders": {"row_count": {"min": 5}}}}
    (shop / "base.json").write_text(json.dumps(existing))
    error = api12.fail(
        "proposals_contract",
        "input.contract_conflict",
        decisions=str(shop / "d.json"),
        output=str(shop / "out.json"),
        merge=str(shop / "base.json"),
    )
    assert f"{ROWS} conflicts with orders.row_count.min in {shop / 'base.json'}" == error["message"]
    assert not (shop / "out.json").exists()
    code = main(
        ["proposals", "contract", "--decisions", str(shop / "d.json"), "-o", str(shop / "o2.json"),
         "--merge", str(shop / "base.json")]
    )  # fmt: skip
    assert code == 2 and not (shop / "o2.json").exists()


def test_contract_errors(shop, api12):
    accept(api12, shop)
    decisions, out = str(shop / "d.json"), str(shop / "o.json")
    api12.fail("proposals_contract", "input.invalid_value", decisions=decisions, output=out)
    api12.fail("proposals_contract", "input.not_found", decisions=str(shop / "no.json"), output=out)
    api12.fail("proposals_contract", "usage.missing_argument", decisions=decisions)
    api12.fail("proposals_contract", "usage.missing_argument", output=out)
    accept(api12, shop, UNIQUE)
    api12.fail(
        "proposals_contract", "input.not_found", decisions=decisions, output=out,
        merge=str(shop / "nothing.json"),
    )  # fmt: skip
    (shop / "bad.json").write_text("{not json")
    api12.fail(
        "proposals_contract", "input.invalid_schema", decisions=decisions, output=out,
        merge=str(shop / "bad.json"),
    )  # fmt: skip
    api12.fail(
        "proposals_contract", "io.write_failed", decisions=decisions,
        output=str(shop / "missing_dir" / "o.json"),
    )  # fmt: skip
    assert not (shop / "o.json").exists()


def test_a_1_1_request_cannot_use_proposals_contract(shop, api11):
    response = api11.call(
        "proposals_contract", decisions=str(shop / "d.json"), output=str(shop / "o.json")
    )
    assert response["error"]["code"] == "usage.unknown_command"


def test_proposals_contract_is_declared_writes_files_with_path_annotations():
    from shape.bridge.registry import COMMANDS

    command = COMMANDS["proposals_contract"]
    assert command.effects == ("reads_files", "writes_files") and command.since == "1.2"
    assert {k: a.path for k, a in command.args.items()} == {
        "decisions": "read",
        "output": "write",
        "merge": "read",
    }
