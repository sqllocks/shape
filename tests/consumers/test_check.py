"""W3-13 deliverables 4 and 5: ``shape contracts check-consumers`` and the breakage preview."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from consumer_helpers import FINANCE, MARKETING, contract, make_profile

from shape.cli.main import main


def check(
    capsys: pytest.CaptureFixture[str], profile: Path, *flags: str
) -> tuple[int, dict[str, Any] | None, str]:
    rc = main(["contracts", "check-consumers", str(profile), *flags, "--json"])
    out = capsys.readouterr()
    return rc, json.loads(out.out) if out.out.strip() else None, out.err


def by_name(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["consumer"]: c for c in report["consumers"]}


@pytest.fixture
def at(project, monkeypatch):
    monkeypatch.chdir(project)
    return project


# ---- known answers -----------------------------------------------------------------------------


def test_every_consumer_passes_on_a_compatible_producer(at, tmp_path, capsys):
    prof = make_profile(tmp_path / "now.shape")
    rc, rep, _ = check(capsys, prof)
    assert rc == 0
    assert rep["passed"] is True
    assert rep["summary"] == {"consumers": 2, "passed": 2, "failed": 0}
    assert {c["status"] for c in rep["consumers"]} == {"pass"}


def test_dropping_a_required_column_fails_that_consumer_only(at, tmp_path, capsys):
    prof = make_profile(tmp_path / "now.shape", drop=("orders", "amount"))
    rc, rep, _ = check(capsys, prof)
    assert rc == 1
    c = by_name(rep)
    assert c["finance"]["status"] == "fail"
    assert c["marketing"]["status"] == "pass"
    assert rep["summary"] == {"consumers": 2, "passed": 1, "failed": 1}
    rules = {(v["table"], v["column"], v["rule"]) for v in c["finance"]["violations"]}
    assert ("orders", "amount", "required_column") in rules


def test_a_producer_table_the_consumer_does_not_name_never_causes_a_violation(at, tmp_path, capsys):
    """The producer profile has an ``audit`` table and extra columns no contract mentions."""
    prof = make_profile(tmp_path / "now.shape")
    rc, rep, _ = check(capsys, prof)
    assert rc == 0
    # and removing it changes nothing for the consumers either
    gone = make_profile(tmp_path / "gone.shape", drop_table="audit")
    rc, rep, _ = check(capsys, gone)
    assert rc == 0
    # a consumer of a single table passes even when the producer has many more columns
    assert all(not c["violations"] for c in rep["consumers"])


def test_extra_columns_never_cause_a_violation(at, tmp_path, capsys):
    narrow = {"tables": {"orders": {"required_columns": ["order_id"]}}}
    (at / "contracts" / "consumers" / "narrow.json").write_text(
        json.dumps(contract("narrow", narrow)), encoding="utf-8"
    )
    rc, rep, _ = check(capsys, make_profile(tmp_path / "now.shape"))
    assert rc == 0
    assert by_name(rep)["narrow"]["status"] == "pass"


def test_a_removed_table_is_named(at, tmp_path, capsys):
    rc, rep, _ = check(capsys, make_profile(tmp_path / "now.shape", drop_table="customer"))
    assert rc == 1
    (v,) = by_name(rep)["marketing"]["violations"]
    assert (v["table"], v["column"], v["rule"]) == ("customer", None, "table_exists")
    assert by_name(rep)["finance"]["status"] == "pass"


def test_changed_type_and_null_rate_rules(at, tmp_path, capsys):
    rc, rep, _ = check(capsys, make_profile(tmp_path / "now.shape", amount_text=True))
    assert rc == 1
    rules = {v["rule"] for v in by_name(rep)["finance"]["violations"]}
    assert "dtype" in rules
    rc, rep, _ = check(capsys, make_profile(tmp_path / "n.shape", note_null=0.0))
    assert rc == 0


def test_report_names_consumer_and_producer_owners(at, tmp_path, capsys):
    prof = make_profile(tmp_path / "now.shape", drop=("orders", "amount"))
    rc, rep, _ = check(capsys, prof)
    fin = by_name(rep)["finance"]
    assert fin["owner"] == "finance@example.com"
    assert fin["since"] == "2026-10-03"
    assert fin["file"].endswith("finance.json")
    amount = [v for v in fin["violations"] if v["column"] == "amount"]
    assert amount and all(v["producer_owner"] == "finance-data@example.com" for v in amount)
    # a column without an owner in shape.yml carries none
    prof = make_profile(tmp_path / "two.shape", drop=("customer", "segment"))
    rc, rep, _ = check(capsys, prof)
    seg = [v for v in by_name(rep)["marketing"]["violations"] if v["column"] == "segment"]
    assert seg and "producer_owner" not in seg[0]


def test_report_layout(at, tmp_path, capsys):
    out = tmp_path / "report.json"
    prof = make_profile(tmp_path / "now.shape")
    rc = main(["contracts", "check-consumers", str(prof), "-o", str(out)])
    text = capsys.readouterr().out
    assert rc == 0 and "2 of 2 consumers pass" in text
    rep = json.loads(out.read_text())
    assert rep["format"] == "shape-consumer-check"
    assert rep["version"] == 1 and isinstance(rep["version"], int)
    assert rep["source"] == "orders"
    assert rep["profile"]["name"] == "orders" and len(rep["profile"]["content_id"]) == 64
    assert "baseline" not in rep
    assert set(rep["consumers"][0]) == {
        "consumer",
        "owner",
        "source",
        "since",
        "file",
        "status",
        "violations",
    }


def test_text_output_shows_violations_and_owners(at, tmp_path, capsys):
    prof = make_profile(tmp_path / "now.shape", drop=("orders", "amount"))
    rc = main(["contracts", "check-consumers", str(prof)])
    text = capsys.readouterr().out
    assert rc == 1
    assert "FAIL  finance" in text and "owner: finance@example.com" in text
    assert "orders.amount: required_column" in text
    assert "producer owner: finance-data@example.com" in text
    assert "PASS  marketing" in text


# ---- breakage preview ------------------------------------------------------------------------


def test_baseline_lists_the_violations_a_change_introduced(at, tmp_path, capsys):
    base = make_profile(tmp_path / "base.shape")
    new = make_profile(tmp_path / "new.shape", drop=("orders", "amount"))
    rc, rep, _ = check(capsys, new, "--baseline", str(base))
    assert rc == 1
    fin = by_name(rep)["finance"]
    assert fin["broken_by_change"] is True and fin["already_failing"] is False
    assert all(v["new"] for v in fin["violations"])
    mk = by_name(rep)["marketing"]
    assert mk["status"] == "pass" and mk["broken_by_change"] is False
    assert rep["summary"]["broken_by_change"] == 1
    assert rep["baseline"]["name"] == "orders" and len(rep["baseline"]["content_id"]) == 64


def test_baseline_separates_old_failures_from_new_ones(at, tmp_path, capsys):
    """The baseline already lacked ``order_id`` (finance was broken before); the change drops
    ``amount`` too. Only the second is new."""
    base = make_profile(tmp_path / "base.shape", drop=("orders", "order_id"))
    both = make_profile(
        tmp_path / "both.shape", drop=(("orders", "amount"), ("orders", "order_id"))
    )
    rc, rep, _ = check(capsys, both, "--baseline", str(base))
    fin = by_name(rep)["finance"]
    new_rules = {(v["column"], v["rule"]) for v in fin["violations"] if v["new"]}
    old_rules = {(v["column"], v["rule"]) for v in fin["violations"] if not v["new"]}
    assert ("amount", "required_column") in new_rules
    assert ("order_id", "required_column") in old_rules
    assert fin["broken_by_change"] is True


def test_baseline_marks_a_consumer_that_was_already_failing(at, tmp_path, capsys):
    base = make_profile(tmp_path / "base.shape", drop=("orders", "amount"))
    same = make_profile(tmp_path / "same.shape", drop=("orders", "amount"))
    rc, rep, _ = check(capsys, same, "--baseline", str(base))
    fin = by_name(rep)["finance"]
    assert rc == 1  # a broken consumer still fails the run
    assert fin["status"] == "fail" and fin["broken_by_change"] is False
    assert fin["already_failing"] is True
    assert not any(v["new"] for v in fin["violations"])
    assert rep["summary"]["broken_by_change"] == 0


def test_baseline_text_marks_new_violations(at, tmp_path, capsys):
    base = make_profile(tmp_path / "base.shape")
    new = make_profile(tmp_path / "new.shape", drop=("orders", "amount"))
    assert main(["contracts", "check-consumers", str(new), "--baseline", str(base)]) == 1
    text = capsys.readouterr().out
    assert "broken by this change" in text and "[new]" in text


def test_baseline_that_is_not_a_profile_is_exit_2(at, tmp_path, capsys):
    bad = tmp_path / "base.shape"
    bad.write_text("nope")
    rc, _, err = check(capsys, make_profile(tmp_path / "n.shape"), "--baseline", str(bad))
    assert rc == 2 and "not a .shape profile" in err


# ---- which contracts run ---------------------------------------------------------------------


def test_contracts_of_other_sources_do_not_run(at, tmp_path, capsys):
    other = contract(
        "billing", {"tables": {"orders": {"required_columns": ["nope"]}}}, source="invoices"
    )
    (at / "contracts" / "consumers" / "billing.json").write_text(json.dumps(other))
    rc, rep, _ = check(capsys, make_profile(tmp_path / "now.shape"))
    assert rc == 0 and set(by_name(rep)) == {"finance", "marketing"}


def test_explicit_source_and_folder(tmp_path, capsys):
    d = tmp_path / "elsewhere"
    d.mkdir()
    (d / "a.json").write_text(json.dumps(contract("a", FINANCE, source="orders")))
    (d / "b.json").write_text(json.dumps(contract("b", MARKETING, source="customers")))
    prof = make_profile(tmp_path / "now.shape", drop=("orders", "amount"))
    rc, rep, _ = check(capsys, prof, "--consumers", str(d), "--source", "orders")
    assert rc == 1 and set(by_name(rep)) == {"a"}
    rc, rep, _ = check(capsys, prof, "--consumers", str(d), "--source", "customers")
    assert rc == 0 and set(by_name(rep)) == {"b"}


def test_default_folder_is_next_to_shape_yml(project, tmp_path, capsys, monkeypatch):
    sub = project / "deep" / "er"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    rc, rep, _ = check(capsys, make_profile(tmp_path / "now.shape"))
    assert rc == 0 and set(by_name(rep)) == {"finance", "marketing"}
    assert Path(rep["directory"]) == project / "contracts" / "consumers"


def test_subfolders_of_the_consumers_folder_are_read(at, tmp_path, capsys):
    team = at / "contracts" / "consumers" / "team-x"
    team.mkdir()
    (team / "x.json").write_text(json.dumps(contract("x", FINANCE)))
    rc, rep, _ = check(capsys, make_profile(tmp_path / "now.shape"))
    assert "x" in by_name(rep)


def test_no_consumers_passes_unless_required(at, tmp_path, capsys):
    for f in (at / "contracts" / "consumers").glob("*.json"):
        f.unlink()
    prof = make_profile(tmp_path / "now.shape")
    rc, rep, _ = check(capsys, prof)
    assert rc == 0 and rep["consumers"] == [] and rep["passed"] is True
    rc, rep, err = check(capsys, prof, "--require-consumers")
    assert rc == 2 and "no consumer contract for source 'orders'" in err
    # also when the folder does not exist at all
    (at / "contracts" / "consumers").rmdir()
    rc, rep, _ = check(capsys, prof)
    assert rc == 0
    rc, _, err = check(capsys, prof, "--require-consumers")
    assert rc == 2


def test_require_consumers_is_satisfied_by_a_match(at, tmp_path, capsys):
    rc, rep, _ = check(capsys, make_profile(tmp_path / "now.shape"), "--require-consumers")
    assert rc == 0


# ---- unusable input: exit 2 -------------------------------------------------------------------


def test_a_bad_consumer_file_lists_every_problem(at, tmp_path, capsys):
    d = at / "contracts" / "consumers"
    (d / "bad.json").write_text(json.dumps(contract("bad", {}, since="soon")))
    (d / "worse.json").write_text("{nope")
    rc, rep, err = check(capsys, make_profile(tmp_path / "now.shape"))
    # W1-14: --json prints the shape-result document with the error, and no report
    assert rc == 2 and rep is not None and rep["exit_code"] == 2 and "consumers" not in rep
    assert "bad.json" in err and "$.since" in err and "worse.json" in err


def test_a_bad_file_of_another_source_is_still_bad(at, tmp_path, capsys):
    (at / "contracts" / "consumers" / "other.json").write_text(
        json.dumps(contract("o", FINANCE, source="invoices", version=9))
    )
    rc, _, err = check(capsys, make_profile(tmp_path / "now.shape"))
    assert rc == 2 and "newer Shape" in err


def test_duplicate_consumer_names_are_exit_2(at, tmp_path, capsys):
    d = at / "contracts" / "consumers"
    (d / "finance2.json").write_text(json.dumps(contract("finance", FINANCE)))
    rc, _, err = check(capsys, make_profile(tmp_path / "now.shape"))
    assert rc == 2 and "also defined in" in err


def test_unknown_source_is_exit_2(at, tmp_path, capsys):
    rc, _, err = check(capsys, make_profile(tmp_path / "now.shape"), "--source", "nope")
    assert rc == 2 and "no source 'nope'" in err


def test_without_a_project_a_source_must_be_named(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    prof = make_profile(tmp_path / "now.shape")
    rc, _, err = check(capsys, prof)
    assert rc == 2 and "--source" in err


def test_explicit_missing_folder_is_exit_2(at, tmp_path, capsys):
    rc, _, err = check(
        capsys, make_profile(tmp_path / "now.shape"), "--consumers", str(tmp_path / "nowhere")
    )
    assert rc == 2 and "nowhere" in err


def test_profile_problems_are_exit_2(at, tmp_path, capsys):
    rc, _, err = check(capsys, tmp_path / "missing.shape")
    assert rc == 2
    csv = tmp_path / "x.csv"
    csv.write_text("a\n1\n")
    rc, _, err = check(capsys, csv)
    assert rc == 2 and "not a .shape profile" in err


def test_no_project_ignores_shape_yml_but_takes_a_source_name(at, tmp_path, capsys):
    prof = make_profile(tmp_path / "now.shape", drop=("orders", "amount"))
    rc, rep, _ = check(capsys, prof, "--no-project", "--source", "orders")
    assert rc == 1  # contracts/consumers/ in the working folder still holds the contracts
    assert "producer_owner" not in json.dumps(rep)  # shape.yml's owners were not read
    rc, _, err = check(capsys, prof, "--no-project", "--project", str(at / "shape.yml"))
    assert rc == 2 and "--no-project" in err


def test_several_sources_need_a_choice(tmp_path, capsys, monkeypatch):
    (tmp_path / "shape.yml").write_text(
        "format: shape-project\nversion: 1\nname: p\nsources:\n  a:\n    path: data/a\n"
        "  b:\n    path: data/b\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    rc, _, err = check(capsys, make_profile(tmp_path / "now.shape"))
    assert rc == 2 and "--source" in err
    # a profile named like a source picks it
    rc, rep, _ = check(capsys, make_profile(tmp_path / "b.shape", name="b"))
    assert rc == 0 and rep["source"] == "b"


# ---- single-table producers and contracts that do not fit ------------------------------------


def test_single_table_producer(tmp_path, capsys):
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq

    import shape

    pq.write_table(
        pa.table({"id": np.arange(1, 101), "v": np.arange(100) * 1.5}), tmp_path / "t.parquet"
    )
    prof = tmp_path / "t.shape"
    shape.save(shape.profile(str(tmp_path / "t.parquet"), name="t"), str(prof))
    d = tmp_path / "consumers"
    d.mkdir()
    ok = {"required_columns": ["id", "v"], "columns": {"v": {"dtype": "float"}}}
    bad = {"required_columns": ["id", "gone"]}
    (d / "ok.json").write_text(json.dumps(contract("ok", ok, source="t")))
    (d / "bad.json").write_text(json.dumps(contract("bad", bad, source="t")))
    rc, rep, _ = check(capsys, prof, "--source", "t", "--consumers", str(d))
    assert rc == 1
    assert by_name(rep)["ok"]["status"] == "pass"
    (v,) = by_name(rep)["bad"]["violations"]
    assert (v["table"], v["column"], v["rule"]) == (None, "gone", "required_column")


def test_a_contract_that_does_not_fit_the_producer_fails_that_consumer(tmp_path, capsys):
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq

    import shape

    pq.write_table(pa.table({"id": np.arange(1, 11)}), tmp_path / "t.parquet")
    prof = tmp_path / "t.shape"
    shape.save(shape.profile(str(tmp_path / "t.parquet"), name="t"), str(prof))
    d = tmp_path / "consumers"
    d.mkdir()
    (d / "a.json").write_text(json.dumps(contract("a", FINANCE, source="t")))
    rc, rep, _ = check(capsys, prof, "--source", "t", "--consumers", str(d))
    assert rc == 1
    (v,) = by_name(rep)["a"]["violations"]
    assert v["rule"] == "contract_not_applicable"


def test_row_count_and_joint_rules_are_checked(tmp_path, capsys, at):
    rules = {
        "tables": {
            "orders": {"row_count": {"min": 10_000}},
            "customer": {"columns": {"age": {"max": 20}}},
        }
    }
    (at / "contracts" / "consumers" / "volume.json").write_text(
        json.dumps(contract("volume", rules))
    )
    rc, rep, _ = check(capsys, make_profile(tmp_path / "now.shape"))
    assert rc == 1
    got = {(v["table"], v["column"], v["rule"]) for v in by_name(rep)["volume"]["violations"]}
    assert ("orders", None, "row_count.min") in got
    assert ("customer", "age", "max") in got


def test_a_rule_a_safe_capture_cannot_evaluate_is_reported_never_passed(tmp_path, at):
    """INT-18 (W3-13 x W1-11): a consumer's `max` on a column whose extremes a safe capture
    removed is a violation that says so; with a full capture it is measured."""
    import shape
    from shape.consumers.check import check_consumers
    from shape.consumers.contract import ConsumerContract

    rules = {"tables": {"customer": {"columns": {"age": {"max": 200}}}}}
    (at / "contracts" / "consumers" / "ages.json").write_text(json.dumps(contract("ages", rules)))
    from shape.consumers.check import find_files, load_all

    contracts: list[ConsumerContract] = load_all(find_files(at / "contracts" / "consumers"))
    ages = [c for c in contracts if c.consumer == "ages"]
    safe = shape.load(str(make_profile(tmp_path / "safe.shape")))
    (entry,) = check_consumers(safe, ages)
    assert entry["status"] == "fail"
    (v,) = entry["violations"]
    assert (v["table"], v["column"], v["rule"], v["expected"]) == ("customer", "age", "max", 200)
    assert v["observed"].startswith("not evaluable: ")
    full = shape.load(str(make_profile(tmp_path / "full.shape", capture="full")))
    (entry,) = check_consumers(full, ages)
    assert entry["status"] == "pass" and entry["violations"] == []
