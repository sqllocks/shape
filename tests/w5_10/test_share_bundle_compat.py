"""W5-10: the ``shape-share-attestation`` format, version 1, stays readable (a signed golden bundle
written by the first release of the format and never regenerated)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from shape import share_bundle

GOLDEN = Path(__file__).parent / "golden"
BUNDLE = GOLDEN / "bundle_v1.zip"
PUBLIC = GOLDEN / "bundle_v1_public_key.hex"
FIELDS = {
    "format",
    "version",
    "dataset_id",
    "shape_version",
    "tables",
    "manifest_sha256",
    "checks",
    "key_id",
    "signature",
}


def attestation() -> dict:
    with zipfile.ZipFile(BUNDLE) as zf:
        return json.loads(zf.read("attestation.json"))


def test_golden_attestation_declares_format_and_version_1():
    att = attestation()
    assert set(att) == FIELDS
    assert att["format"] == "shape-share-attestation" and att["version"] == 1
    assert [c["name"] for c in att["checks"]] == ["memorization", "top_values"]
    for check in att["checks"]:
        assert set(check) == {"name", "parameters", "passed", "counts"}


def test_golden_bundle_verifies_with_this_release():
    result = share_bundle.verify(BUNDLE)
    assert result.ok, result.lines


@pytest.mark.sign
def test_golden_signature_still_verifies():
    public = bytes.fromhex(PUBLIC.read_text().strip())
    assert share_bundle.verify(BUNDLE, public).ok
    assert not share_bundle.verify(BUNDLE, bytes(32)).ok
