"""W3-02 deliverable 5: a contract v1 from the accepted rules, ``--merge``, and the acceptance runs
on the shipped retail domain (``shape check`` passes on the profile it came from; a contract built
from the clean data fails on data corrupted with ``shape chaos`` corruptions)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.csv as pacsv
import pytest

import shape
from shape.chaos.groundtruth import Corruption, corrupt_tables
from shape.cli.main import main
from shape.profile.reference.profile import save
from shape.proposals import (
    DecisionError,
    DecisionFile,
    RuleConflictError,
    dump_contract,
    propose_rules,
)

from .conftest import LATER, NOW


def orders(n: int = 80, **override) -> pa.Table:
    cols = {
        "order_id": list(range(n)),
        "status": [("new", "paid", "void")[i % 3] for i in range(n)],
        "amount": [10.5 + (i % 20) for i in range(n)],
    }
    cols.update(override)
    return pa.table(cols)


def decided(profile, accept=lambda pid: True, **kw) -> DecisionFile:
    f = DecisionFile.empty()
    f.update(propose_rules(profile, **kw), kinds=["rule"], now=NOW)
    for e in f.entries():
        if accept(e.proposal.id):
            f.decide(e.proposal.id, "accepted", actor="ana", now=LATER)
    return f


@pytest.fixture(scope="module")
def prof():
    return shape.profile({"orders": orders()}, joint=True)


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


# ---- DecisionFile.to_contract ----


def test_the_contract_holds_the_accepted_rules_and_shape_check_passes_on_the_profile(prof):
    f = decided(prof)
    contract = f.to_contract()
    assert set(contract) == {"tables"} and set(contract["tables"]) == {"orders"}
    table = contract["tables"]["orders"]
    assert table["row_count"] == {"min": 40, "max": 120}
    assert table["columns"]["order_id"]["unique"] is True
    assert table["columns"]["status"]["allowed_values"] == ["new", "paid", "void"]
    result = shape.check(prof, contract)
    assert result.passed, result.violations


def test_only_accepted_non_stale_rules_are_written(prof):
    f = decided(prof, accept=lambda pid: pid.endswith(".dtype"))
    f.decide("rule:orders.amount.dtype", "rejected", actor="ana", now=LATER)
    f.decide("rule:orders.status.dtype", "deferred", actor="ana", now=LATER)
    f.update(propose_rules(shape.profile({"orders": orders(order_id=[5] * 80)})),
             kinds=["rule"], now=LATER)  # fmt: skip
    contract = f.to_contract()
    cols = contract["tables"]["orders"]["columns"]
    assert cols["order_id"] == {"dtype": "integer"}
    assert "amount" not in cols and "status" not in cols  # rejected, deferred
    assert "row_count" not in contract["tables"]["orders"]  # pending


def test_a_decision_file_with_no_accepted_rule_has_no_contract(prof):
    f = decided(prof, accept=lambda pid: False)
    with pytest.raises(DecisionError, match="no accepted, non-stale rule"):
        f.to_contract()
    with pytest.raises(DecisionError, match="no accepted"):
        DecisionFile.empty().to_contract()


def test_the_same_decisions_give_the_same_bytes_whatever_the_order_they_were_made_in(prof):
    a = decided(prof)
    b = DecisionFile.empty()
    props = propose_rules(prof)
    b.update(list(reversed(props)), kinds=["rule"], now=NOW)
    for e in reversed(b.entries()):
        b.decide(e.proposal.id, "accepted", actor="bo", now=LATER)
    text = dump_contract(a.to_contract())
    assert text == dump_contract(b.to_contract())
    assert text == dump_contract(DecisionFile.loads(a.dumps()).to_contract())
    assert text.endswith("}\n") and json.loads(text) == a.to_contract()
    assert text == json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n"  # keys sorted


def test_dependencies_are_written_as_contract_rules(prof):
    t = pa.table(
        {"zip": [10000 + i % 10 for i in range(100)], "city": [f"c{i % 10}" for i in range(100)]}
    )
    p = shape.profile({"t": t}, joint=True)
    f = decided(p, accept=lambda pid: ".fd." in pid)
    contract = f.to_contract()
    rules = contract["tables"]["t"]["fd"]
    assert {"determinant": "city", "dependent": "zip", "min_confidence": 0.99} in rules
    assert shape.check(p, contract).passed


def test_single_table_rules_make_a_flat_contract_shape_check_reads():
    p = shape.profile(orders())
    contract = decided(p).to_contract()
    assert "tables" not in contract and "columns" in contract and "row_count" in contract
    assert shape.check(p, contract).passed


# ---- --merge ----


def test_merge_adds_the_rules_to_an_existing_contract_and_leaves_the_rest(prof):
    existing = {
        "tables": {
            "orders": {"columns": {"amount": {"nullable": False}, "tax": {"dtype": "float"}}},
            "customers": {"row_count": {"min": 1}},
        },
        "drift": {"thresholds": {"null_rate": 0.1}},
    }
    keep = copy.deepcopy(existing)
    f = decided(prof)
    merged = f.to_contract(existing)
    assert existing == keep  # not changed in place
    assert merged["drift"] == keep["drift"] and merged["tables"]["customers"] == {
        "row_count": {"min": 1}
    }
    cols = merged["tables"]["orders"]["columns"]
    assert cols["tax"] == {"dtype": "float"} and cols["amount"]["nullable"] is False
    assert cols["amount"]["dtype"] == "float" and cols["order_id"]["unique"] is True


def test_a_rule_that_agrees_with_the_existing_contract_is_not_a_conflict(prof):
    f = decided(prof)
    once = f.to_contract()
    assert f.to_contract(once) == once  # merging into the contract it made changes nothing


@pytest.mark.parametrize(
    ("existing", "proposal", "where"),
    [
        ({"tables": {"orders": {"columns": {"amount": {"min": 0}}}}},
         "rule:orders.amount.range", "orders.amount.min"),
        ({"tables": {"orders": {"columns": {"amount": {"max": 5}}}}},
         "rule:orders.amount.range", "orders.amount.max"),
        ({"tables": {"orders": {"columns": {"amount": {"dtype": "string"}}}}},
         "rule:orders.amount.dtype", "orders.amount.dtype"),
        ({"tables": {"orders": {"columns": {"status": {"allowed_values": ["new"]}}}}},
         "rule:orders.status.allowed_values", "orders.status.allowed_values"),
        ({"tables": {"orders": {"row_count": {"min": 5}}}},
         "rule:orders.row_count", "orders.row_count.min"),
    ],
)  # fmt: skip
def test_an_accepted_rule_that_conflicts_with_one_in_the_existing_contract_names_both(
    prof, existing, proposal, where
):
    f = decided(prof, accept=lambda pid: pid == proposal)
    with pytest.raises(RuleConflictError) as err:
        f.to_contract(existing, merge_source="EXISTING.json")
    assert str(err.value) == f"{proposal} conflicts with {where} in EXISTING.json"
    assert isinstance(err.value, DecisionError)


def test_a_dependency_with_another_threshold_conflicts():
    t = pa.table(
        {"zip": [10000 + i % 10 for i in range(100)], "city": [f"c{i % 10}" for i in range(100)]}
    )
    f = decided(
        shape.profile({"t": t}, joint=True), accept=lambda pid: pid == "rule:t.fd.city->zip"
    )
    existing = {
        "tables": {
            "t": {"fd": [{"determinant": "city", "dependent": "zip", "min_confidence": 0.9}]}
        }
    }
    with pytest.raises(
        RuleConflictError, match=r"rule:t.fd.city->zip conflicts with t.fd.city->zip in X"
    ):
        f.to_contract(existing, merge_source="X")
    same = {
        "tables": {
            "t": {"fd": [{"determinant": "city", "dependent": "zip", "min_confidence": 0.99}]}
        }
    }
    assert f.to_contract(same) == same


def test_merging_a_flat_rule_into_a_dataset_contract_or_back_is_refused(prof):
    flat = shape.profile(orders())
    with pytest.raises(DecisionError, match="single-table profile but the contract.*'tables'"):
        decided(flat).to_contract({"tables": {"orders": {}}})
    with pytest.raises(DecisionError, match="multi-table profile but the contract"):
        decided(prof).to_contract({"columns": {}})
    with pytest.raises(DecisionError, match="must be a JSON object"):
        decided(prof).to_contract([])  # type: ignore[arg-type]
    with pytest.raises(DecisionError, match="must be an object"):
        decided(prof).to_contract({"tables": {"orders": {"columns": []}}})


def test_a_flat_conflict_names_the_table_from_the_rule_id():
    flat = shape.profile(orders())
    name = flat.name
    f = decided(flat, accept=lambda pid: pid.endswith("amount.range"))
    with pytest.raises(
        RuleConflictError,
        match=rf"rule:{name}.amount.range conflicts with {name}.amount.min in E.json",
    ):
        f.to_contract({"columns": {"amount": {"min": 0}}}, merge_source="E.json")


# ---- the command line ----


def test_the_whole_flow_through_the_cli_ends_in_a_passing_shape_check(tmp_path, capsys):
    data = tmp_path / "data"
    data.mkdir()
    pacsv.write_csv(orders(), data / "orders.csv")
    p = tmp_path / "orders.shape"
    assert main(["profile", str(data), "--dataset", "--joint", "-o", str(p)]) == 0
    capsys.readouterr()
    dec, out = tmp_path / "decisions.json", tmp_path / "contract.json"
    code, so, _ = run(capsys, "proposals", "propose", str(p), "-d", str(dec), "--kinds", "rule")
    summary = json.loads(so)
    assert code == 0 and summary["added"] > 10 and summary["stale"] == []
    assert json.loads(dec.read_text())["version"] == 2
    code, so, _ = run(
        capsys, "proposals", "list", "-d", str(dec), "--kind", "rule", "--min-confidence", "0.8"
    )
    assert code == 0 and "rule:orders.order_id.dtype" in so
    ids = [
        json.loads(dec.read_text())["proposals"][i]["id"]
        for i in range(len(json.loads(dec.read_text())["proposals"]))
    ]
    for pid in ids:
        assert (
            run(capsys, "proposals", "decide", "-d", str(dec), pid, "accept", "--actor", "ana")[0]
            == 0
        )
    code, so, _ = run(capsys, "proposals", "contract", "-d", str(dec), "-o", str(out))
    assert code == 0 and json.loads(so) == {"written": str(out), "rules": len(ids)}
    again = tmp_path / "again.json"
    run(capsys, "proposals", "contract", "-d", str(dec), "-o", str(again))
    assert out.read_bytes() == again.read_bytes()  # same decisions, same bytes
    code, so, _ = run(capsys, "check", str(p), str(out))
    assert code == 0, so


def test_contract_merge_exits_2_on_a_conflict_naming_both_rules(tmp_path, capsys):
    p = tmp_path / "o.shape"
    save(shape.profile({"orders": orders()}), p, capture="full")
    dec, out, existing = tmp_path / "d.json", tmp_path / "c.json", tmp_path / "EXISTING.json"
    run(capsys, "proposals", "propose", str(p), "-d", str(dec), "--kinds", "rule")
    run(
        capsys,
        "proposals",
        "decide",
        "-d",
        str(dec),
        "rule:orders.amount.range",
        "accept",
        "--actor",
        "ana",
    )
    existing.write_text(json.dumps({"tables": {"orders": {"columns": {"amount": {"min": 99}}}}}))
    code, _, err = run(
        capsys, "proposals", "contract", "-d", str(dec), "-o", str(out), "--merge", str(existing)
    )
    assert code == 2 and not out.exists()
    assert (
        err.strip()
        == f"shape: error: rule:orders.amount.range conflicts with orders.amount.min in {existing}"
    )
    existing.write_text(
        json.dumps({"tables": {"orders": {"columns": {"tax": {"dtype": "float"}}}}, "drift": {}})
    )
    code, so, _ = run(
        capsys, "proposals", "contract", "-d", str(dec), "-o", str(out), "--merge", str(existing)
    )
    merged = json.loads(out.read_text())
    assert code == 0 and merged["tables"]["orders"]["columns"]["tax"] == {"dtype": "float"}
    assert "min" in merged["tables"]["orders"]["columns"]["amount"] and merged["drift"] == {}


def test_contract_command_errors_are_exit_2(tmp_path, capsys):
    p = tmp_path / "o.shape"
    save(shape.profile({"orders": orders()}), p, capture="full")
    dec, out = tmp_path / "d.json", tmp_path / "c.json"
    run(capsys, "proposals", "propose", str(p), "-d", str(dec), "--kinds", "rule")
    code, _, err = run(capsys, "proposals", "contract", "-d", str(dec), "-o", str(out))
    assert code == 2 and "no accepted" in err and not out.exists()
    run(
        capsys,
        "proposals",
        "decide",
        "-d",
        str(dec),
        "rule:orders.row_count",
        "accept",
        "--actor",
        "a",
    )
    code, _, err = run(
        capsys,
        "proposals",
        "contract",
        "-d",
        str(dec),
        "-o",
        str(out),
        "--merge",
        str(tmp_path / "no.json"),
    )
    assert code != 0 and "not found" in err
    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    code, _, err = run(
        capsys, "proposals", "contract", "-d", str(dec), "-o", str(out), "--merge", str(bad)
    )
    assert code == 2 and "not valid JSON" in err
    code, _, _ = run(
        capsys, "proposals", "contract", "-d", str(tmp_path / "missing.json"), "-o", str(out)
    )
    assert code != 0


def test_several_profiles_through_the_cli_for_rules_only(tmp_path, capsys):
    paths = []
    for d in range(3):
        path = tmp_path / f"day{d}.shape"
        save(
            shape.profile(
                {
                    "orders": orders(
                        80 + 10 * d, order_id=list(range(1000 * d, 1000 * d + 80 + 10 * d))
                    )
                }
            ),
            path,
        )
        paths.append(str(path))
    dec = tmp_path / "d.json"
    code, so, _ = run(capsys, "proposals", "propose", *paths, "-d", str(dec), "--kinds", "rule")
    assert code == 0
    doc = json.loads(dec.read_text())
    assert {p["evidence"]["profiles"] for p in doc["proposals"]} == {3}
    band = next(p for p in doc["proposals"] if p["id"] == "rule:orders.row_count")
    assert band["claim"]["tables"]["orders"]["row_count"] == {"min": 60, "max": 125}
    code, _, err = run(
        capsys, "proposals", "propose", *paths, "-d", str(dec), "--kinds", "rule,pii"
    )
    assert code == 2 and "rule only" in err
    code, _, err = run(capsys, "proposals", "propose", *paths, "-d", str(dec))
    assert code == 2 and "rule only" in err


def test_the_cli_kind_lists_match_the_package():
    from shape.cli.proposals import _DEFAULT_KINDS, _KINDS
    from shape.proposals import DEFAULT_KINDS, KINDS

    assert _KINDS == KINDS and _DEFAULT_KINDS == DEFAULT_KINDS and "rule" in KINDS


# ---- acceptance: the shipped domain ----


@pytest.fixture(scope="module")
def retail():
    from shape.generation.domains import load_domain
    from shape.generation.engine import Engine

    return Engine(load_domain("retail").schema, scale="small", seed=11).generate().tables


@pytest.fixture(scope="module")
def retail_decisions(retail):
    return decided(shape.profile(retail, joint=True), min_confidence=0.5)


def test_the_contract_from_proposals_of_the_retail_profile_passes_shape_check_on_it(
    retail, retail_decisions
):
    prof = shape.profile(retail, joint=True)
    contract = retail_decisions.to_contract()
    assert set(contract["tables"]) == set(retail)
    result = shape.check(prof, contract)
    assert result.passed, result.violations[:5]


def test_the_retail_contract_also_holds_on_a_second_capture_of_the_same_domain(
    retail, retail_decisions
):
    from shape.generation.domains import load_domain
    from shape.generation.engine import Engine

    other = Engine(load_domain("retail").schema, scale="small", seed=12).generate().tables
    first = shape.profile(retail, joint=True)
    second = shape.profile(other, joint=True)
    f = decided([first, second])
    contract = f.to_contract()
    assert shape.check(first, contract).passed and shape.check(second, contract).passed


def violated(prof, contract) -> set[tuple[str, str]]:
    return {(v["column"], v["rule"]) for v in shape.check(prof, contract).violations}


def test_corrupted_retail_data_fails_the_contract_built_on_the_clean_data(retail, retail_decisions):
    contract = retail_decisions.to_contract()
    clean = shape.profile(retail, joint=True)
    never_null = clean.tables["customer"]["columns"]["first_name"]["null_count"]
    assert never_null == 0 and clean.tables["customer"]["columns"]["customer_id"]["is_unique"]

    def corrupted(*corruptions):
        out = corrupt_tables(retail, list(corruptions), seed=3)
        return violated(shape.profile(out.tables, joint=True), contract)

    dup = corrupted(Corruption("duplicates", 0.05, table="customer"))
    assert ("customer.customer_id", "unique") in dup

    creep = corrupted(Corruption("null_creep", 0.05, table="customer", column="first_name"))
    assert ("customer.first_name", "nullable") in creep

    # the profile reads digit strings as integers again, so the dtype rule cannot see a number
    # delivered as text; the range rule can: the observed minimum is text, not a number
    typed = corrupted(Corruption("type_change", 0.05, table="product", column="unit_price"))
    assert {("product.unit_price", "min"), ("product.unit_price", "max")} & typed

    for case, table in ((dup, "customer"), (creep, "customer"), (typed, "product")):
        assert all(c is None or c.startswith(table) for c, _ in case)  # only that table is blamed


def test_rules_can_be_proposed_with_the_other_kinds_from_one_profile(tmp_path, capsys):
    p = tmp_path / "o.shape"
    save(shape.profile({"orders": orders()}), p, capture="full")
    dec = tmp_path / "d.json"
    code, so, _ = run(capsys, "proposals", "propose", str(p), "-d", str(dec), "--kinds", "pii,rule")
    assert code == 0 and json.loads(so)["added"] > 5
    kinds = {x["kind"] for x in json.loads(dec.read_text())["proposals"]}
    assert "rule" in kinds
    # the default kinds leave the rule proposals alone
    code, so, _ = run(capsys, "proposals", "propose", str(p), "-d", str(dec))
    assert code == 0 and json.loads(so)["withdrawn"] == 0
    assert "rule" in {x["kind"] for x in json.loads(dec.read_text())["proposals"]}


def test_the_rules_are_documented():
    doc = (Path(__file__).parents[2] / "docs" / "PROPOSALS.md").read_text(encoding="utf-8")
    cli = (Path(__file__).parents[2] / "docs" / "CLI.md").read_text(encoding="utf-8")
    section = doc[doc.index("\n## Rules\n") : doc.index("\n## The decision file\n")]
    for needle in (
        "shape proposals contract", "--merge", "--kinds rule", "rule:TABLE.COLUMN.RULE",
        "rule:TABLE.fd.DETERMINANT->DEPENDENT", "30 non-null values", "10% of its span",
        "strength   = 1 - (10 / (support + 10)) / profiles", "Sensitive columns", "stale",
        "conflicts with", "shape check",
        "decisions-v2.schema.json", "dtype", "nullable", "unique", "range", "pattern",
        "allowed_values", "no_placeholder", "row_count", "reference_pair", "fd",
    ):  # fmt: skip
        assert needle in section or needle in doc, needle
    for kind, cap in __import__("shape.proposals.rules", fromlist=["CAPS"]).CAPS.items():
        assert f"`{kind}`" in section and f"{cap:.2f}" in section, kind
    assert "shape proposals propose|list|decide|contract" in cli and "PROPOSALS.md#rules" in cli
