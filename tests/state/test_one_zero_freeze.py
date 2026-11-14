"""The approved document controls changes to the 1.0 compatibility baseline."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path

import pytest

from shape import compat

ROOT = Path(__file__).resolve().parents[2]
BASELINE_FILE = ROOT / "docs/specs/freeze-1.0.json"
BASELINE = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def test_baseline_requires_a_freeze_document_revision() -> None:
    text = (ROOT / "docs/plans/1.0-FREEZE.md").read_text(encoding="utf-8")
    match = re.search(r"Baseline SHA-256: `([0-9a-f]{64})`", text)
    assert match, "the approved freeze must identify its enforcement baseline"
    assert hashlib.sha256(BASELINE_FILE.read_bytes()).hexdigest() == match[1], (
        "revise docs/plans/1.0-FREEZE.md for a reviewed baseline change"
    )
    assert BASELINE["format"] == "shape-one-zero-freeze"
    assert BASELINE["version"] == 1
    assert BASELINE["reader_window"] == {
        "through": "1.x",
        "earliest_removal": "2.0.0",
        "notice_releases": 1,
        "offline_migrator_required": True,
    }


@pytest.mark.parametrize("name", BASELINE["files"])
def test_frozen_file_hash(name: str) -> None:
    assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == BASELINE["files"][name], (
        f"{name}: frozen surface changed; revise the freeze document and baseline together"
    )


def test_reader_registry_matches_the_approved_window() -> None:
    actual = {name: asdict(kind) for name, kind in compat.KINDS.items()}
    # JSON normalizes tuples and integer mapping keys in the persisted baseline.
    assert json.loads(json.dumps(actual)) == BASELINE["readers"], (
        "reader declarations changed; revise the freeze document for a compatible addition"
    )


PAIRS = [
    (name, int(v)) for name, kind in BASELINE["readers"].items() for v in kind["first_release"]
]


@pytest.mark.parametrize(("name", "version"), PAIRS)
def test_every_frozen_version_remains_readable(name: str, version: int) -> None:
    frozen = BASELINE["readers"][name]
    kind = compat.KINDS[name]
    minimum = frozen["first_release"][str(version)]
    assert minimum == "0.9.0"
    doc = {"format": frozen["format"], "version": version, "min_shape_version": minimum}
    with compat.strict_formats(False):
        compat.check_format(name, doc)
        assert compat.check_readable(name, doc) == version
        for old_key in frozen["legacy_version_keys"]:
            legacy = {old_key: version}
            assert compat.check_readable(name, legacy) == version
        if frozen["implicit_version"] == version:
            assert compat.check_readable(name, {}) == version
    if frozen["unified_in_body"]:
        stamped = compat.stamp(name, version=version)
        assert stamped["min_shape_version"] == minimum
        assert stamped["version"] == version
        for old_key in frozen["legacy_version_keys"]:
            assert stamped[old_key] == version
    else:
        with pytest.raises(ValueError, match="content-addressed"):
            compat.stamp(name, version=version)
    with pytest.raises(compat.UnsupportedVersionError) as caught:
        compat.check_readable(name, {"version": kind.current + 1, "min_shape_version": "2.0.0"})
    assert caught.value.min_shape_version == "2.0.0"
