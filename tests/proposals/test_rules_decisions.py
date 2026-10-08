"""W3-02 deliverable 4: the decision file version 2 (rule proposals, the status ``stale``), and
its compatibility with version 1."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pytest

import shape
from shape.cli.main import main
from shape.proposals import (
    MAX_VERSION,
    DecisionError,
    DecisionFile,
    Proposal,
    propose,
    propose_rules,
)

from .conftest import LATER, NOW
from .test_schema_compat import CORPUS, validate

HERE = Path(__file__).parent
SCHEMA_V2 = Path(shape.__file__).parent / "schemas" / "decisions-v2.schema.json"
CORPUS_V2 = HERE / "data" / "decisions_v2.json"
LAST = "2026-10-05T09:00:00Z"


def orders(n: int = 60, **override) -> pa.Table:
    cols = {
        "order_id": list(range(n)),
        "status": [("new", "paid", "void")[i % 3] for i in range(n)],
        "amount": [10.0 + (i % 20) for i in range(n)],
    }
    cols.update(override)
    return pa.table(cols)


def profile_of(**kw):
    return shape.profile({"orders": orders(**kw)})


def rule_file(**kw) -> DecisionFile:
    f = DecisionFile.empty()
    f.update(propose_rules(profile_of(**kw)), kinds=["rule"], now=NOW)
    return f


def schema_v2():
    return json.loads(SCHEMA_V2.read_text(encoding="utf-8"))


# ---- version 1 stays byte for byte; version 2 for rules ----


def test_a_file_with_no_rule_proposal_is_still_written_as_version_1():
    f = DecisionFile.empty()
    f.update([Proposal("pii:a.x", "pii", "a.x", {"pii": "email"}, 0.9, {})], now=NOW)
    assert json.loads(f.dumps())["version"] == 1
    assert (
        DecisionFile.empty().dumps().startswith('{\n  "format": "shape-decisions",\n  "version": 1')
    )


@pytest.mark.parametrize("path", CORPUS, ids=lambda p: p.name)
def test_a_frozen_version_1_file_loads_and_is_rewritten_byte_identical(path, tmp_path):
    text = path.read_text(encoding="utf-8")
    f = DecisionFile.loads(text)
    assert f.version == 1 and f.dumps() == text
    out = tmp_path / "again.json"
    f.write(out)
    assert out.read_bytes() == path.read_bytes()
    # a run that adds no rule proposal does not move the version either
    f.update([], kinds=["semantic"], now=NOW)
    assert json.loads(f.dumps())["version"] == 1


def test_a_file_with_a_rule_proposal_is_written_as_version_2():
    f = rule_file()
    doc = json.loads(f.dumps())
    assert doc["version"] == 2 and f.version == 2
    assert {p["kind"] for p in doc["proposals"]} == {"rule"}
    assert validate(doc, schema_v2(), schema_v2()) == []


def test_the_version_follows_the_content_a_withdrawn_last_rule_goes_back_to_version_1():
    f = rule_file()
    assert f.version == 2
    f.update([], kinds=["rule"], now=LAST)  # pending rules the run no longer finds are withdrawn
    assert f.version == 1 and json.loads(f.dumps())["proposals"] == []


def test_a_version_2_file_round_trips_byte_for_byte():
    text = CORPUS_V2.read_text(encoding="utf-8")
    f = DecisionFile.loads(text)
    assert f.version == 2 and f.dumps() == text
    assert DecisionFile.loads(f.dumps()).dumps() == text
    schema = schema_v2()
    assert validate(json.loads(text), schema, schema) == []
    kinds = {e.proposal.kind for e in f.entries()}
    statuses = {e.status for e in f.entries()}
    assert kinds == {"pii", "rule"} and statuses == {
        "pending",
        "accepted",
        "rejected",
        "deferred",
        "stale",
    }


def test_the_version_2_schema_ships_and_names_the_format_and_version():
    schema = schema_v2()
    assert schema["properties"]["format"]["const"] == "shape-decisions"
    assert schema["properties"]["version"]["const"] == 2
    assert "rule" in schema["$defs"]["proposal"]["properties"]["kind"]["enum"]
    assert "stale" in schema["$defs"]["decision"]["properties"]["status"]["enum"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(version=1),
        lambda d: d.update(version=3),
        lambda d: d["proposals"][0].update(kind="mystery"),
        lambda d: d["decisions"][0].update(status="maybe"),
        lambda d: d.update(extra=1),
    ],
)
def test_the_version_2_schema_rejects_what_the_reader_rejects(mutate):
    doc = json.loads(CORPUS_V2.read_text(encoding="utf-8"))
    mutate(doc)
    assert validate(doc, schema_v2(), schema_v2())
    if doc.get("version") == 1:  # a v1 file holding rules or a stale status is refused
        with pytest.raises(DecisionError, match="version 2"):
            DecisionFile.from_dict(doc)
    else:
        with pytest.raises(DecisionError):
            DecisionFile.from_dict(doc)


def test_a_version_3_file_is_refused_with_the_newer_version_message():
    doc = json.loads(CORPUS_V2.read_text(encoding="utf-8")) | {"version": 3}
    with pytest.raises(DecisionError, match=r"version 3.*newer than the version 2.*upgrade"):
        DecisionFile.from_dict(doc)
    assert MAX_VERSION == 2


def test_a_version_1_file_cannot_hold_a_rule_or_a_stale_decision():
    doc = json.loads(CORPUS_V2.read_text(encoding="utf-8"))
    as_v1 = doc | {"version": 1}
    with pytest.raises(DecisionError, match="rule proposals need version 2"):
        DecisionFile.from_dict(as_v1)
    only_status = json.loads(CORPUS[0].read_text(encoding="utf-8"))
    only_status["decisions"][0]["status"] = "stale"
    with pytest.raises(DecisionError, match="status must be one of accepted, rejected, deferred"):
        DecisionFile.from_dict(only_status)


def test_a_person_cannot_decide_stale():
    f = rule_file()
    pid = next(iter(f.entries())).proposal.id
    with pytest.raises(DecisionError, match="status must be one of"):
        f.decide(pid, "stale", actor="ana")


def test_the_cli_refuses_a_version_3_file_with_exit_2(tmp_path, capsys):
    dec = tmp_path / "d.json"
    dec.write_text(
        json.dumps({"format": "shape-decisions", "version": 3, "proposals": [], "decisions": []})
    )
    assert main(["proposals", "list", "-d", str(dec)]) == 2
    assert "version 3" in capsys.readouterr().err


# ---- stale ----


def accept_all(f: DecisionFile, ids=None) -> None:
    for e in f.entries():
        if ids is None or e.proposal.id in ids:
            f.decide(e.proposal.id, "accepted", actor="ana", note="ok", now=LATER)


def test_an_accepted_rule_whose_evidence_no_longer_holds_is_marked_stale_not_dropped():
    f = rule_file()
    accept_all(f)
    before = {e.proposal.id: e.proposal for e in f.entries()}
    dup = profile_of(order_id=[7] * 60)  # the key now repeats
    result = f.update(propose_rules(dup, decisions=f), kinds=["rule"], now=LAST)
    assert "rule:orders.order_id.unique" in result.stale
    entry = {e.proposal.id: e for e in f.entries()}["rule:orders.order_id.unique"]
    assert entry.status == "stale"
    assert entry.proposal == before["rule:orders.order_id.unique"]  # claim and evidence kept
    assert entry.decision.actor == "ana" and entry.decision.at == LAST
    assert "accepted by ana at 2026-10-04T08:30:00Z (ok)" in entry.decision.note
    assert "no longer supports" in entry.decision.note
    assert set(before) <= {e.proposal.id for e in f.entries()}  # nothing was dropped
    assert [e.proposal.id for e in f.list(status="stale")] == sorted(result.stale)
    assert json.loads(f.dumps())["version"] == 2


def test_stale_rules_are_listed_by_the_cli(tmp_path, capsys):
    prof = tmp_path / "p.shape"
    from shape.profile.reference.profile import save

    save(profile_of(), prof, capture="full")
    dec = tmp_path / "d.json"
    assert main(["proposals", "propose", str(prof), "-d", str(dec), "--kinds", "rule"]) == 0
    capsys.readouterr()
    uid = "rule:orders.order_id.unique"
    assert main(["proposals", "decide", "-d", str(dec), uid, "accept", "--actor", "ana"]) == 0
    dup = tmp_path / "dup.shape"
    save(profile_of(order_id=[7] * 60), dup, capture="full")
    capsys.readouterr()
    assert main(["proposals", "propose", str(dup), "-d", str(dec), "--kinds", "rule"]) == 0
    assert json.loads(capsys.readouterr().out)["stale"] == [uid]
    assert main(["proposals", "list", "-d", str(dec), "--status", "stale"]) == 0
    out = capsys.readouterr().out
    assert uid in out and "stale" in out and "by ana" in out
    assert main(["proposals", "list", "-d", str(dec), "--status", "accepted"]) == 0
    assert uid not in capsys.readouterr().out


def test_an_accepted_claim_is_kept_while_it_still_holds_after_a_re_profile():
    f = rule_file()
    accept_all(f, {"rule:orders.amount.range", "rule:orders.row_count"})
    claim = {e.proposal.id: e.proposal.claim for e in f.entries()}
    # a re-profile of a slightly different day: its own range and band differ; accepted ones hold
    later = profile_of(n=64, amount=[11.0 + (i % 18) for i in range(64)])
    fresh = {p.id: p for p in propose_rules(later)}
    assert fresh["rule:orders.amount.range"].claim != claim["rule:orders.amount.range"]
    result = f.update(propose_rules(later, decisions=f), kinds=["rule"], now=LAST)
    assert result.stale == ()
    now = {e.proposal.id: e for e in f.entries()}
    for pid in ("rule:orders.amount.range", "rule:orders.row_count"):
        assert now[pid].status == "accepted" and now[pid].proposal.claim == claim[pid]
    assert now["rule:orders.amount.range"].proposal.evidence["rows"] == 64  # fresh evidence


def test_an_accepted_claim_that_stops_holding_goes_stale_even_if_a_new_rule_is_proposed():
    f = rule_file()
    accept_all(f, {"rule:orders.amount.range"})
    wild = profile_of(amount=[1000.0 + i for i in range(60)])
    result = f.update(propose_rules(wild, decisions=f), kinds=["rule"], now=LAST)
    assert result.stale == ("rule:orders.amount.range",)
    entry = {e.proposal.id: e for e in f.entries()}["rule:orders.amount.range"]
    assert (
        entry.status == "stale"
        and entry.proposal.claim["tables"]["orders"]["columns"]["amount"]["max"] < 1000
    )


def test_without_the_decisions_a_changed_claim_still_never_changes_what_was_accepted():
    f = rule_file()
    accept_all(f, {"rule:orders.amount.range"})
    old = {e.proposal.id: e for e in f.entries()}["rule:orders.amount.range"].proposal
    later = profile_of(n=64, amount=[11.0 + (i % 18) for i in range(64)])
    result = f.update(propose_rules(later), kinds=["rule"], now=LAST)  # no decisions=
    assert "rule:orders.amount.range" in result.stale
    assert {e.proposal.id: e for e in f.entries()}["rule:orders.amount.range"].proposal == old


def test_a_stale_rule_stays_stale_until_a_person_accepts_it_again():
    f = rule_file()
    accept_all(f, {"rule:orders.order_id.unique"})
    f.update(propose_rules(profile_of(order_id=[7] * 60), decisions=f), kinds=["rule"], now=LAST)
    # the key is unique again: the rule is proposed again, but it is not accepted again by itself
    f.update(propose_rules(profile_of(), decisions=f), kinds=["rule"], auto_accept=0.0, now=LAST)
    e = {x.proposal.id: x for x in f.entries()}["rule:orders.order_id.unique"]
    assert e.status == "stale"
    columns = f.to_contract()["tables"]["orders"]["columns"]  # a stale rule is not written
    assert "unique" not in columns["order_id"]
    f.decide(e.proposal.id, "accepted", actor="bo", note="key is back", now=LAST)
    assert {x.proposal.id: x for x in f.entries()}[e.proposal.id].status == "accepted"


def test_a_pending_rule_that_a_re_profile_no_longer_finds_is_withdrawn():
    f = rule_file()
    assert "rule:orders.order_id.unique" in {e.proposal.id for e in f.entries()}
    result = f.update(
        propose_rules(profile_of(order_id=[7] * 60), decisions=f), kinds=["rule"], now=LAST
    )
    assert "rule:orders.order_id.unique" in result.withdrawn
    assert "rule:orders.order_id.unique" not in {e.proposal.id for e in f.entries()}


def test_deferred_and_rejected_rules_are_not_made_stale_or_dropped():
    f = rule_file()
    f.decide("rule:orders.order_id.unique", "deferred", actor="ana", now=LATER)
    f.decide("rule:orders.order_id.nullable", "rejected", actor="ana", now=LATER)
    f.update(
        propose_rules(profile_of(order_id=[None] + [7] * 59), decisions=f), kinds=["rule"], now=LAST
    )
    status = {e.proposal.id: e.status for e in f.entries()}
    assert status["rule:orders.order_id.unique"] == "deferred"
    assert status["rule:orders.order_id.nullable"] == "rejected"


def test_a_rejected_rule_is_never_proposed_again():
    f = rule_file()
    pid = "rule:orders.status.allowed_values"
    f.decide(pid, "rejected", actor="ana", note="more statuses are coming", now=LATER)
    result = f.update(propose_rules(profile_of(), decisions=f), kinds=["rule"], now=LAST)
    assert pid in result.skipped_rejected
    e = {x.proposal.id: x for x in f.entries()}[pid]
    assert e.status == "rejected"
    wider = profile_of(status=[("new", "paid", "void", "lost")[i % 4] for i in range(60)])
    result = f.update(propose_rules(wider, decisions=f), kinds=["rule"], now=LAST)
    assert pid in result.skipped_rejected
    assert {x.proposal.id: x for x in f.entries()}[pid].proposal == e.proposal


def test_a_run_that_does_not_ask_for_rules_leaves_rules_alone():
    f = rule_file()
    accept_all(f, {"rule:orders.order_id.unique"})
    pending = {e.proposal.id for e in f.list(status="pending")}
    assert pending
    result = f.update([], now=LAST)  # the default kinds: relationship, pii, semantic
    assert result.withdrawn == () and result.stale == ()
    assert pending <= {e.proposal.id for e in f.entries()}
    assert {x.proposal.id: x for x in f.entries()}[
        "rule:orders.order_id.unique"
    ].status == "accepted"


def test_nothing_is_accepted_automatically_unless_asked():
    f = rule_file()
    assert not f.list(status="accepted")
    g = DecisionFile.empty()
    g.update(propose_rules(profile_of()), kinds=["rule"], auto_accept=0.8, now=NOW)
    auto = g.list(status="accepted")
    assert auto and {e.decision.actor for e in auto} == {"auto-accept"}
    assert all(e.proposal.confidence >= 0.8 for e in auto)
    assert len(auto) < len(g.entries())  # the less confident ones wait for a person


def test_propose_through_the_api_with_a_decision_file_keeps_accepted_rules():
    f = DecisionFile.empty()
    prof = profile_of()
    f.update(propose(prof, kinds=["rule"], decisions=f), kinds=["rule"], now=NOW)
    accept_all(f, {"rule:orders.row_count"})
    again = f.update(propose(prof, kinds=["rule"], decisions=f), kinds=["rule"], now=LAST)
    assert again.stale == () and again.added == () and again.updated == ()
