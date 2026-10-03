"""W1-02 deliverable 3: the same propose, review and persist pattern for PII and semantics."""

from __future__ import annotations

import pyarrow as pa

import shape
from shape.proposals import DecisionFile, propose, propose_pii, propose_semantics

from .conftest import NOW


def by_id(ps):
    return {p.id: p for p in ps}


def test_pii_is_proposed_from_values_with_the_match_rate_as_evidence(profile, tables):
    got = by_id(propose_pii(profile, tables))
    p = got["pii:customers.email"]
    assert p.kind == "pii" and p.claim == {"pii": "email"}
    assert p.evidence["values"]["matched"] == p.evidence["values"]["sampled"] == 50
    assert p.confidence >= 0.9


def test_pii_is_proposed_from_names_alone_with_lower_confidence(profile, tables):
    alone = by_id(propose_pii(profile))
    with_data = by_id(propose_pii(profile, tables))
    assert "values" not in alone["pii:customers.email"].evidence
    assert alone["pii:customers.email"].evidence["name"]["hint"] == "email"
    assert alone["pii:customers.email"].confidence < with_data["pii:customers.email"].confidence


def test_a_person_name_column_is_proposed_by_name(profile):
    p = by_id(propose_pii(profile))["pii:customers.full_name"]
    assert p.claim == {"pii": "person_name"}


def test_plain_columns_are_not_proposed_as_pii(profile, tables):
    ids = by_id(propose_pii(profile, tables))
    assert not [i for i in ids if "amount" in i or "notes" in i or "status_code" in i]


def test_semantics_are_proposed_with_the_evidence_that_gave_them(profile, tables):
    got = by_id(propose_semantics(profile, tables))
    p = got["semantic:customers.email"]
    assert p.kind == "semantic" and p.claim == {"semantic": "email"}
    assert p.evidence["name"]["hint"] == "email"
    assert 0.0 < p.confidence <= 1.0
    assert "semantic:orders.amount" not in got  # nothing to say about an unnamed number


def test_the_whole_cycle_persists_and_a_rejection_survives_a_reprofile(tmp_path, tables):
    path = tmp_path / "decisions.json"
    prof = shape.profile(tables)
    f = DecisionFile.empty()
    f.update(propose(prof, tables), now=NOW)
    f.decide("pii:customers.full_name", "rejected", actor="ana", note="fictional names", now=NOW)
    f.decide("pii:customers.email", "accepted", actor="ana", now=NOW)
    f.write(path)

    # re-profile (different row count) and propose again
    bigger = {**tables, "customers": pa.concat_tables([tables["customers"]] * 2)}
    f2 = DecisionFile.read(path)
    res = f2.update(propose(shape.profile(bigger), bigger), now=NOW)
    assert "pii:customers.full_name" in res.skipped_rejected
    states = {e.proposal.id: e.status for e in f2.entries()}
    assert states["pii:customers.full_name"] == "rejected"
    assert states["pii:customers.email"] == "accepted"
    assert [e.proposal.id for e in f2.accepted("pii")] == ["pii:customers.email"]
