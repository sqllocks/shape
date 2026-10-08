"""INT-18: W1-11's safe capture and W3-08's multivariate joint entries.

The copula keeps every category value of its columns and a cohort its most common value, and all
four entries name their columns in lists: a safe capture keeps none of them, says so in the
redaction manifest, and a full capture keeps them all."""

from __future__ import annotations

import warnings
from typing import Any

import pytest

import shape
from shape.artifact.io import read_artifact

ENTRIES = ("multivariate_outliers", "pca", "cohorts", "copula")


@pytest.fixture(scope="module")
def profile(table: Any) -> Any:
    """The planted table with the multivariate entries (opt-in since INT-18)."""
    return shape.profile(table, multivariate=True)


def _load(path: Any) -> Any:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return shape.load(str(path))


def test_the_profile_holds_the_entries(profile: Any) -> None:
    joint = profile.to_dict()["joint"]
    assert "copula" in joint and "categorical_columns" in joint


def test_a_safe_capture_keeps_no_multivariate_entry(profile: Any, tmp_path: Any) -> None:
    shape.save(profile, str(tmp_path / "s.shape"))
    joint = _load(tmp_path / "s.shape").to_dict()["joint"]
    assert not set(ENTRIES) & set(joint)
    assert "categorical_columns" not in joint
    manifest, _ = read_artifact(str(tmp_path / "s.shape"), notice=False)
    assert manifest["redaction_manifest"]["joint"].startswith("removed: the multivariate entries")


def test_a_full_capture_keeps_them(profile: Any, tmp_path: Any) -> None:
    shape.save(profile, str(tmp_path / "f.shape"), capture="full")
    joint = _load(tmp_path / "f.shape").to_dict()["joint"]
    held = [e for e in ENTRIES if e in profile.to_dict()["joint"]]
    assert held and all(e in joint for e in held)


def test_saving_a_safe_capture_again_keeps_the_record(profile: Any, tmp_path: Any) -> None:
    shape.save(profile, str(tmp_path / "s.shape"))
    shape.save(_load(tmp_path / "s.shape"), str(tmp_path / "again.shape"))
    first, _ = read_artifact(str(tmp_path / "s.shape"), notice=False)
    again, _ = read_artifact(str(tmp_path / "again.shape"), notice=False)
    assert again["redaction_manifest"] == first["redaction_manifest"]
