"""W1-02 deliverable 5: the command line and the Python API."""

from __future__ import annotations

import json

import pyarrow.csv as pacsv
import pytest

import shape
from shape.cli.main import main

from .conftest import shop_tables
from .test_relationships import CUST


@pytest.fixture
def workdir(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    for name, t in shop_tables().items():
        pacsv.write_csv(t, data / f"{name}.csv")
    prof = tmp_path / "p.shape"
    assert main(["profile", str(data), "--dataset", "-o", str(prof)]) == 0
    return tmp_path


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_propose_list_decide_and_generate_through_the_cli(workdir, capsys):
    prof, dec, data = workdir / "p.shape", workdir / "decisions.json", workdir / "data"
    code, out, _ = run(
        capsys, "proposals", "propose", str(prof), "--data", str(data), "-d", str(dec)
    )
    assert code == 0 and json.loads(out)["added"] > 0 and dec.exists()

    code, out, _ = run(capsys, "proposals", "list", "-d", str(dec), "--status", "pending", "--json")
    rows = json.loads(out)["payload"]
    ids = {r["id"] for r in rows}
    assert CUST in ids and all(r["status"] == "pending" for r in rows)
    assert all({"kind", "confidence", "evidence", "id", "status"} <= set(r) for r in rows)

    code, out, _ = run(
        capsys,
        "proposals",
        "list",
        "-d",
        str(dec),
        "--kind",
        "relationship",
        "--min-confidence",
        "0.9",
    )
    assert code == 0 and CUST in out and "pii:" not in out

    code, out, _ = run(
        capsys, "proposals", "decide", "-d", str(dec), CUST, "accept",
        "--actor", "ana", "--note", "orders belong to customers",
    )  # fmt: skip
    assert code == 0
    code, out, _ = run(
        capsys, "proposals", "list", "-d", str(dec), "--status", "accepted", "--json"
    )
    (row,) = json.loads(out)["payload"]
    assert row["decision"]["actor"] == "ana" and row["decision"]["note"].startswith("orders")

    # generation keeps the accepted relationship
    gen = ["generate", "--from", str(prof), "--decisions", str(dec), "--dry-run", "--json"]
    code, out, _ = run(capsys, *gen)
    assert code == 0
    assert json.loads(out)  # a plan, not an error
    code, out, _ = run(capsys, "plan", str(prof), "--decisions", str(dec))
    assert code == 0 and "relationship:fk_orders_customer_id" in out


def test_a_rejected_proposal_is_not_proposed_again_by_the_cli(workdir, capsys):
    prof, dec, data = workdir / "p.shape", workdir / "decisions.json", workdir / "data"
    run(capsys, "proposals", "propose", str(prof), "--data", str(data), "-d", str(dec))
    assert (
        run(capsys, "proposals", "decide", "-d", str(dec), CUST, "reject", "--actor", "a")[0] == 0
    )
    code, out, _ = run(
        capsys, "proposals", "propose", str(prof), "--data", str(data), "-d", str(dec)
    )
    assert code == 0 and CUST in json.loads(out)["skipped_rejected"]
    code, out, _ = run(capsys, "proposals", "list", "-d", str(dec), "--status", "pending", "--json")
    assert CUST not in {r["id"] for r in json.loads(out)["payload"]}


def test_cli_auto_accept_is_off_unless_asked(workdir, capsys):
    prof, dec, data = workdir / "p.shape", workdir / "decisions.json", workdir / "data"
    run(capsys, "proposals", "propose", str(prof), "--data", str(data), "-d", str(dec))
    assert json.loads(dec.read_text())["decisions"] == []
    dec2 = workdir / "auto.json"
    code, out, _ = run(
        capsys, "proposals", "propose", str(prof), "--data", str(data), "-d", str(dec2),
        "--auto-accept", "0.95",
    )  # fmt: skip
    assert code == 0 and json.loads(out)["auto_accepted"]
    assert {d["actor"] for d in json.loads(dec2.read_text())["decisions"]} == {"auto-accept"}


def test_bad_input_is_exit_2_with_one_line(workdir, capsys):
    dec = workdir / "decisions.json"
    code, _, err = run(
        capsys, "proposals", "decide", "-d", str(dec), "x:y", "accept", "--actor", "a"
    )
    assert code == 2 and err.startswith("shape: error:")
    dec.write_text("{}", encoding="utf-8")
    code, _, err = run(capsys, "proposals", "list", "-d", str(dec))
    assert code == 2 and "format" in err
    newer = {"format": "shape-decisions", "version": 99, "proposals": [], "decisions": []}
    dec.write_text(json.dumps(newer), encoding="utf-8")
    code, _, err = run(capsys, "proposals", "list", "-d", str(dec))
    assert code == 2 and "version 99" in err


def test_decide_defaults_the_actor_from_the_environment(workdir, capsys, monkeypatch):
    prof, dec, data = workdir / "p.shape", workdir / "decisions.json", workdir / "data"
    run(capsys, "proposals", "propose", str(prof), "--data", str(data), "-d", str(dec))
    monkeypatch.setenv("SHAPE_ACTOR", "ci-bot")
    assert run(capsys, "proposals", "decide", "-d", str(dec), CUST, "defer")[0] == 0
    (d,) = [d for d in json.loads(dec.read_text())["decisions"] if d["proposal"] == CUST]
    assert d["actor"] == "ci-bot" and d["status"] == "deferred"


def test_data_may_be_given_as_name_equals_path(workdir, capsys):
    prof, dec, data = workdir / "p.shape", workdir / "decisions.json", workdir / "data"
    args = ["proposals", "propose", str(prof), "-d", str(dec), "--kinds", "relationship"]
    for name in ("customers", "orders", "products"):
        args += ["--data", f"{name}={data / (name + '.csv')}"]
    code, out, _ = run(capsys, *args)
    assert code == 0
    ev = {p["id"]: p for p in json.loads(dec.read_text())["proposals"]}[CUST]["evidence"]
    assert "containment" in ev


def test_the_python_api_matches_the_cli(workdir):
    from shape.proposals import DecisionFile, propose

    prof = shape.load(str(workdir / "p.shape"))
    f = DecisionFile.empty()
    f.update(propose(prof, shop_tables()), now="2026-10-03T12:00:00Z")
    assert CUST in {e.proposal.id for e in f.list(status="pending")}


def test_the_new_commands_are_documented():
    from pathlib import Path

    doc = (Path(__file__).parents[2] / "docs" / "PROPOSALS.md").read_text(encoding="utf-8")
    for needle in ("shape proposals propose", "shape proposals list", "shape proposals decide",
                   "shape-decisions", "--auto-accept", "--decisions"):  # fmt: skip
        assert needle in doc


def test_the_cli_kind_list_matches_the_package():
    from shape.cli.proposals import _KINDS
    from shape.proposals import KINDS

    assert _KINDS == KINDS
