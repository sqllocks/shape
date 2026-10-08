"""SEC3: one classification taxonomy behind every release, redaction and propagation path."""

from __future__ import annotations

import pytest

from shape.privacy import (
    LEVELS,
    ClassificationTaxonomy,
    derived_classification,
    policy,
    redact_sensitive,
    release_for,
)
from shape.privacy import classification as taxonomy_module

ALL_LABELS = ("PUBLIC", "INTERNAL", "CONFIDENTIAL", "SENSITIVE", "PII", "SECRET", "TOP_SECRET")


def test_policy_and_taxonomy_share_one_table():
    assert policy.LEVELS is taxonomy_module.LEVELS
    t = ClassificationTaxonomy()
    for label in ALL_LABELS:
        assert LEVELS[label] == t.rank(label)


@pytest.mark.parametrize("label", ALL_LABELS)
def test_every_label_is_accepted_by_every_entry_point(label):
    shape = {"rows": 100, "columns": {"x": {"kind": "text", "count": 100, "topk": [["a", 50]]}}}
    assert derived_classification([label]) == label
    assert release_for(shape, {"x": label}, "PUBLIC").allowed
    assert release_for(shape, {}, "SECRET", source_classification=label).allowed == (
        LEVELS[label] <= LEVELS["SECRET"]
    )
    out = redact_sensitive(shape, {"x": label}, redact_at=("CONFIDENTIAL",))
    assert (out["columns"]["x"].get("value_evidence_redacted") is True) == (
        LEVELS[label] >= LEVELS["CONFIDENTIAL"]
    )


def test_aliases_rank_as_confidential():
    t = ClassificationTaxonomy()
    assert t.rank("PII") == t.rank("SENSITIVE") == t.rank("CONFIDENTIAL") == 2
    assert t.join("PUBLIC", "PII", "INTERNAL") == "PII"
    assert t.permits("pii", "confidential") and not t.permits("SECRET", "SENSITIVE")


def test_confidential_is_released_and_redacted_like_sensitive():
    shape = {"rows": 100, "columns": {"x": {"kind": "text", "count": 100, "topk": [["a", 50]]}}}
    a = release_for(shape, {"x": "CONFIDENTIAL"}, "INTERNAL")
    b = release_for(shape, {"x": "SENSITIVE"}, "INTERNAL")
    assert a.removed == b.removed and "topk" in str(a.removed)


def test_unknown_label_is_rejected_not_ignored():
    with pytest.raises(ValueError):
        ClassificationTaxonomy().rank("TOPSECRET")
    with pytest.raises(ValueError):
        derived_classification(["NOPE"])
    with pytest.raises(ValueError):
        release_for({"rows": 10, "columns": {"x": {"count": 10}}}, {"x": "NOPE"}, "PUBLIC")
    shape = {"rows": 10, "columns": {"x": {"topk": [["a", 9]]}}}
    with pytest.raises(ValueError):
        redact_sensitive(shape, {"x": "NOPE"})
