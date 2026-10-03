"""W1-02 deliverable 1: the decision file format (text, diffable, versioned)."""

from __future__ import annotations

import json
import re

import pytest

from shape.proposals import (
    FORMAT,
    MAX_VERSION,
    VERSION,
    Decision,
    DecisionError,
    DecisionFile,
    Proposal,
)

from .conftest import LATER, NOW

TIME = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


def prop(id_: str = "pii:t.email", conf: float = 0.9, **kw) -> Proposal:
    kind = id_.split(":", 1)[0]
    return Proposal(
        id=id_,
        kind=kind,
        subject=id_.split(":", 1)[1],
        claim=kw.get("claim", {"pii": "email"}),
        confidence=conf,
        evidence=kw.get("evidence", {"matched": "50/50"}),
    )


def test_a_new_file_is_empty_and_declares_format_and_integer_version():
    doc = DecisionFile.empty().to_dict()
    assert doc["format"] == FORMAT == "shape-decisions"
    assert doc["version"] == VERSION and isinstance(VERSION, int)
    assert doc["proposals"] == [] and doc["decisions"] == []


def test_proposals_carry_evidence_confidence_and_a_utc_proposal_time():
    f = DecisionFile.empty()
    f.update([prop(conf=0.87654321)], now=NOW)
    p = f.to_dict()["proposals"][0]
    assert p["evidence"] == {"matched": "50/50"}
    assert p["confidence"] == 0.8765  # rounded so a re-profile does not churn the diff
    assert TIME.match(p["proposed_at"]) and p["proposed_at"] == NOW


def test_a_decision_records_actor_time_note_and_status():
    f = DecisionFile.empty()
    f.update([prop()], now=NOW)
    d = f.decide("pii:t.email", "accepted", actor="ana", note="confirmed with the owner", now=LATER)
    assert isinstance(d, Decision)
    row = f.to_dict()["decisions"][0]
    assert row == {
        "proposal": "pii:t.email",
        "status": "accepted",
        "actor": "ana",
        "at": LATER,
        "note": "confirmed with the owner",
    }


@pytest.mark.parametrize("status", ["accepted", "rejected", "deferred"])
def test_all_three_decisions_are_recordable(status):
    f = DecisionFile.empty()
    f.update([prop()], now=NOW)
    f.decide("pii:t.email", status, actor="a", now=NOW)
    assert f.entries()[0].status == status


def test_unknown_status_unknown_proposal_and_blank_actor_are_errors():
    f = DecisionFile.empty()
    f.update([prop()], now=NOW)
    with pytest.raises(DecisionError, match="status"):
        f.decide("pii:t.email", "maybe", actor="a", now=NOW)
    with pytest.raises(DecisionError, match="no proposal"):
        f.decide("pii:t.nope", "accepted", actor="a", now=NOW)
    with pytest.raises(DecisionError, match="actor"):
        f.decide("pii:t.email", "accepted", actor="  ", now=NOW)


def test_deciding_again_replaces_the_decision_not_appends():
    f = DecisionFile.empty()
    f.update([prop()], now=NOW)
    f.decide("pii:t.email", "deferred", actor="a", now=NOW)
    f.decide("pii:t.email", "accepted", actor="b", now=LATER)
    rows = f.to_dict()["decisions"]
    assert len(rows) == 1 and rows[0]["status"] == "accepted" and rows[0]["actor"] == "b"


def test_serialization_is_stable_sorted_and_minimal_for_git(tmp_path):
    a = DecisionFile.empty()
    a.update([prop("pii:b.x"), prop("pii:a.x"), prop("pii:c.x")], now=NOW)
    b = DecisionFile.empty()
    b.update([prop("pii:c.x"), prop("pii:a.x"), prop("pii:b.x")], now=NOW)
    assert a.dumps() == b.dumps()
    ids = [p["id"] for p in json.loads(a.dumps())["proposals"]]
    assert ids == sorted(ids)
    text = a.dumps()
    assert text.endswith("\n") and "\r" not in text
    # one decision touches only its own lines
    a.decide("pii:b.x", "rejected", actor="a", now=LATER)
    before, after = text.splitlines(), a.dumps().splitlines()
    removed = [line for line in before if line not in after]
    assert removed == [] or all("decisions" in line or "]" in line for line in removed)
    p = tmp_path / "d.json"
    a.write(p)
    assert p.read_text(encoding="utf-8") == a.dumps()
    assert DecisionFile.read(p).dumps() == a.dumps()


def test_a_reprofile_with_the_same_evidence_does_not_change_the_file():
    f = DecisionFile.empty()
    f.update([prop()], now=NOW)
    before = f.dumps()
    result = f.update([prop()], now=LATER)
    assert f.dumps() == before
    assert result.added == () and result.updated == ()


def test_changed_evidence_updates_the_proposal_and_keeps_its_original_time_and_decision():
    f = DecisionFile.empty()
    f.update([prop(conf=0.7)], now=NOW)
    f.decide("pii:t.email", "accepted", actor="a", now=NOW)
    result = f.update([prop(conf=0.95, evidence={"matched": "49/50"})], now=LATER)
    assert result.updated == ("pii:t.email",)
    row = f.to_dict()["proposals"][0]
    assert row["confidence"] == 0.95 and row["proposed_at"] == NOW
    assert f.entries()[0].status == "accepted"


def test_a_pending_proposal_that_is_no_longer_found_is_withdrawn_but_a_decided_one_stays():
    f = DecisionFile.empty()
    f.update([prop("pii:a.x"), prop("pii:b.x")], now=NOW)
    f.decide("pii:a.x", "accepted", actor="a", now=NOW)
    result = f.update([], now=LATER)
    assert result.withdrawn == ("pii:b.x",)
    assert [e.proposal.id for e in f.entries()] == ["pii:a.x"]


def test_update_only_withdraws_within_the_kinds_it_ran():
    f = DecisionFile.empty()
    f.update([prop("pii:a.x"), prop("semantic:a.x", claim={"semantic": "email"})], now=NOW)
    f.update([], kinds=("pii",), now=LATER)
    assert [e.proposal.id for e in f.entries()] == ["semantic:a.x"]


# --- reading: strict, newer versions named, nothing guessed -------------------------------------


def valid_doc() -> dict:
    f = DecisionFile.empty()
    f.update([prop()], now=NOW)
    f.decide("pii:t.email", "accepted", actor="a", note="n", now=LATER)
    return f.to_dict()


def test_a_newer_version_is_refused_with_a_message_that_names_both_versions():
    # version 2 exists since W3-02 (rule proposals); the first version this Shape cannot read is 3
    doc = valid_doc() | {"version": MAX_VERSION + 1}
    with pytest.raises(DecisionError, match=rf"version {MAX_VERSION + 1}.*(newer|upgrade)"):
        DecisionFile.from_dict(doc)


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda d: d.update(format="other"), "format"),
        (lambda d: d.update(version="1"), "version"),
        (lambda d: d.update(version=0), "version"),
        (lambda d: d.update(version=True), "version"),
        (lambda d: d.pop("proposals"), "proposals"),
        (lambda d: d.update(surprise=1), "unknown key"),
        (lambda d: d["proposals"][0].update(confidence=1.5), "confidence"),
        (lambda d: d["proposals"][0].update(confidence="high"), "confidence"),
        (lambda d: d["proposals"][0].update(proposed_at="yesterday"), "proposed_at"),
        (
            lambda d: d["proposals"][0].update(proposed_at="2026-10-03T12:00:00+02:00"),
            "proposed_at",
        ),
        (lambda d: d["decisions"][0].update(status="maybe"), "status"),
        (lambda d: d["decisions"][0].update(at="2026-10-03"), "at"),
        (lambda d: d["decisions"][0].update(proposal="pii:zzz.q"), "no proposal"),
        (lambda d: d["decisions"].append(dict(d["decisions"][0])), "twice"),
        (lambda d: d["proposals"].append(dict(d["proposals"][0])), "duplicate"),
        (lambda d: d["proposals"][0].update(kind="telepathy"), "kind"),
    ],
)
def test_invalid_files_are_refused_with_a_specific_message(mutate, match):
    doc = valid_doc()
    mutate(doc)
    with pytest.raises(DecisionError, match=match):
        DecisionFile.from_dict(doc)


def test_not_json_and_not_an_object_are_input_errors(tmp_path):
    p = tmp_path / "d.json"
    p.write_text("{nope", encoding="utf-8")
    with pytest.raises(DecisionError, match="not valid JSON"):
        DecisionFile.read(p)
    p.write_text("[]", encoding="utf-8")
    with pytest.raises(DecisionError, match="JSON object"):
        DecisionFile.read(p)


def test_a_decision_error_is_an_expected_cli_error():
    from shape.cli.errors import EXPECTED

    assert issubclass(DecisionError, EXPECTED)


def test_confidence_boundaries_are_accepted():
    for c in (0.0, 1.0):
        doc = valid_doc()
        doc["proposals"][0]["confidence"] = c
        DecisionFile.from_dict(doc)


# --- auto-accept is off by default -----------------------------------------------------------


def test_nothing_is_accepted_automatically_by_default():
    f = DecisionFile.empty()
    result = f.update([prop(conf=1.0)], now=NOW)
    assert f.to_dict()["decisions"] == [] and result.auto_accepted == ()


def test_auto_accept_when_asked_records_the_actor_and_never_overrides_a_decision():
    f = DecisionFile.empty()
    f.update([prop("pii:a.x", 0.4), prop("pii:b.x", 0.99), prop("pii:c.x", 0.99)], now=NOW)
    f.decide("pii:c.x", "rejected", actor="ana", now=NOW)
    result = f.update(
        [prop("pii:a.x", 0.4), prop("pii:b.x", 0.99), prop("pii:c.x", 0.99)],
        auto_accept=0.95,
        now=LATER,
    )
    assert result.auto_accepted == ("pii:b.x",)
    by = {e.proposal.id: e for e in f.entries()}
    assert by["pii:a.x"].status == "pending"
    assert by["pii:b.x"].status == "accepted" and by["pii:b.x"].decision.actor == "auto-accept"
    assert by["pii:c.x"].status == "rejected"


@pytest.mark.parametrize("bad", [-0.1, 1.1, float("nan")])
def test_auto_accept_threshold_must_be_a_probability(bad):
    with pytest.raises(DecisionError, match="auto-accept"):
        DecisionFile.empty().update([prop()], auto_accept=bad, now=NOW)


def test_rejected_proposals_are_not_proposed_again_even_with_better_evidence():
    f = DecisionFile.empty()
    f.update([prop(conf=0.6)], now=NOW)
    f.decide("pii:t.email", "rejected", actor="a", note="not personal", now=NOW)
    result = f.update([prop(conf=1.0, evidence={"matched": "50/50", "more": 1})], now=LATER)
    assert result.skipped_rejected == ("pii:t.email",)
    assert f.entries()[0].proposal.confidence == 0.6
    assert f.entries()[0].status == "rejected"
    assert f.list(status="pending") == []
