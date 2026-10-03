"""W5-10: the ``shape-fingerprint`` format, version 1, stays readable (golden files written by
the first release of the format and never regenerated)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shape import fingerprint

GOLDEN = Path(__file__).parent / "golden"
UNSIGNED = GOLDEN / "fingerprint_v1_unsigned.json"
SIGNED = GOLDEN / "fingerprint_v1_signed.json"
PUBLIC = GOLDEN / "fingerprint_v1_public_key.hex"
PARQUET = GOLDEN / "people_fingerprint_v1.parquet"
FIELDS = {
    "format",
    "version",
    "synthetic",
    "table",
    "table_id",
    "dataset_id",
    "profile_content_id",
    "reproducibility",
    "key_id",
    "signature",
}


def test_golden_documents_declare_format_and_version_1():
    for path in (UNSIGNED, SIGNED):
        doc = json.loads(path.read_text())
        assert set(doc) == FIELDS
        assert doc["format"] == "shape-fingerprint" and doc["version"] == 1
        assert fingerprint.parse(path.read_text())["table"] == "people"


def test_golden_parquet_verifies_with_this_release():
    outcome = fingerprint.verify(PARQUET)
    assert outcome.ok and outcome.digest_ok and outcome.signature == "unchecked"
    assert outcome.doc == json.loads(SIGNED.read_text())


@pytest.mark.sign
def test_golden_signature_still_verifies():
    public = bytes.fromhex(PUBLIC.read_text().strip())
    doc = json.loads(SIGNED.read_text())
    assert fingerprint.signature_state(doc, public) == "valid"
    assert fingerprint.verify(PARQUET, public).ok
    assert fingerprint.signature_state({**doc, "table": "other"}, public) == "invalid"


def test_unknown_extra_fields_are_tolerated_and_a_newer_version_is_not():
    doc = json.loads(UNSIGNED.read_text())
    assert fingerprint.parse(json.dumps({**doc, "future_field": 1}))["table"] == "people"
    with pytest.raises(fingerprint.FingerprintVersionError):
        fingerprint.parse(json.dumps({**doc, "version": 2}))
