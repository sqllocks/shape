"""Regression tests for the AUD-design findings in proposals and decision files."""

from __future__ import annotations

import pytest

import shape
from shape.proposals import DecisionError, DecisionFile, Proposal, apply_decisions, propose

from .conftest import NOW

# ---- issue 388: what update and decide store must read back ---------------------------------


@pytest.mark.parametrize(
    ("proposal", "match"),
    [
        (Proposal("x", "relationship", "a.b", {}, 0.9, {}), "must be relationship:a.b"),
        (Proposal("zzz:a", "zzz", "a", {}, 0.9, {}), "kind must be one of"),
        (Proposal("pii:", "pii", "", {}, 0.9, {}), "subject must be a non-empty string"),
        (Proposal("pii:a", "pii", "a", [1], 0.9, {}), "claim and evidence must be objects"),  # type: ignore[arg-type]
        (Proposal("pii:a", "pii", "a", {}, 0.9, [1]), "claim and evidence must be objects"),  # type: ignore[arg-type]
    ],
)
def test_update_refuses_a_proposal_the_reader_would_refuse(proposal, match):
    f = DecisionFile.empty()
    with pytest.raises(DecisionError, match=match):
        f.update([proposal], now=NOW)
    assert f.entries() == []  # nothing half-written
    DecisionFile.loads(f.dumps())


@pytest.mark.parametrize("note", [None, 3])
def test_decide_refuses_a_note_that_is_not_text(note):
    f = DecisionFile.empty()
    f.update([Proposal("pii:a", "pii", "a", {}, 0.9, {})], now=NOW)
    with pytest.raises(DecisionError, match="note"):
        f.decide("pii:a", "accepted", actor="ana", note=note, now=NOW)
    assert DecisionFile.loads(f.dumps()).list(status="accepted") == []


# ---- issue 389: apply_decisions on a .shape path ---------------------------------------------


@pytest.mark.parametrize("sub", ["", "tables"])
def test_apply_decisions_accepts_a_shape_path(tmp_path, tables, profile, sub):
    folder = tmp_path / sub if sub else tmp_path
    folder.mkdir(exist_ok=True)
    path = folder / "shop.shape"
    shape.save(profile, str(path))
    f = DecisionFile.empty()
    f.update(propose(profile, tables), now=NOW)
    pid = "relationship:orders.customer_id->customers.customer_id"
    f.decide(pid, "accepted", actor="ana", now=NOW)
    from_path = apply_decisions(str(path), f)
    from_object = apply_decisions(profile, f)
    assert from_path.to_dict()["relationships"] == from_object.to_dict()["relationships"]


# ---- issue 391: impossible times ------------------------------------------------------------


@pytest.mark.parametrize("when", ["2026-13-45T99:99:99Z", "2026-02-30T12:00:00Z"])
def test_an_impossible_time_is_refused(when):
    from shape.proposals.model import stamp

    with pytest.raises(DecisionError, match="2026-10-03T12:00:00Z"):
        stamp(when)
    f = DecisionFile.empty()
    f.update([Proposal("pii:a", "pii", "a", {}, 0.9, {})], now=NOW)
    doc = f.to_dict()
    doc["proposals"][0]["proposed_at"] = when
    with pytest.raises(DecisionError, match="proposed_at"):
        DecisionFile.from_dict(doc)
    f.decide("pii:a", "accepted", actor="ana", now=NOW)
    doc = f.to_dict()
    doc["decisions"][0]["at"] = when
    with pytest.raises(DecisionError, match="at must be UTC"):
        DecisionFile.from_dict(doc)
