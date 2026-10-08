"""W1-11 deliverables 1 and 4: ``shape.save(capture=)``, the artifact record and version."""

from __future__ import annotations

import json
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import shape
from shape import compat
from shape.artifact import codec
from shape.artifact.io import ArtifactError, read_artifact, sha256, write_artifact

Reader = Callable[[Path], str]


def manifest_of(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as z:
        doc = json.loads(z.read("manifest.json"))
    assert isinstance(doc, dict)
    return doc


# --- deliverable 1: the choice, and the default -------------------------------------------------


def test_save_is_safe_by_default(profile: Any, tmp_path: Path, artifact_text: Reader) -> None:
    out = tmp_path / "p.shape"
    shape.save(profile, str(out))
    text = artifact_text(out)
    assert manifest_of(out)["capture"] == {"mode": "safe", "k": 5}
    assert "987654321" not in text


def test_save_full_keeps_what_the_profile_holds(
    profile: Any, tmp_path: Path, artifact_text: Reader, planted: dict[str, str]
) -> None:
    out = tmp_path / "p.shape"
    shape.save(profile, str(out), capture="full")
    assert manifest_of(out)["capture"] == {"mode": "full", "k": None}
    assert planted["extreme"] in artifact_text(out)
    assert shape.load(str(out)).to_dict() == profile.to_dict()


def test_save_rejects_an_unknown_capture(profile: Any, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="capture"):
        shape.save(profile, str(tmp_path / "p.shape"), capture="redacted")
    assert not (tmp_path / "p.shape").exists()


def test_the_in_memory_profile_is_not_changed_by_saving(profile: Any, tmp_path: Path) -> None:
    before = profile.to_dict()
    shape.save(profile, str(tmp_path / "p.shape"))
    assert profile.to_dict() == before
    assert not profile.capture_declared and profile.capture == {"mode": "full", "k": None}


def test_k_column_k_and_classifications_reach_the_file(profile: Any, tmp_path: Path) -> None:
    out = tmp_path / "p.shape"
    shape.save(
        profile,
        str(out),
        k=6,
        column_k={"grade": 12},
        classifications={"age": "CONFIDENTIAL"},
    )
    loaded = shape.load(str(out))
    assert loaded.capture == {"mode": "safe", "k": 6}
    m = loaded.redaction_manifest["tables"]["table"]
    assert m["grade"]["k"] == 12 and m["age"]["sensitive"] is True
    assert loaded.tables["table"]["columns"]["age"]["enum_values"] is None


def test_the_content_id_differs_between_the_two_captures(profile: Any, tmp_path: Path) -> None:
    safe_id = shape.save(profile, str(tmp_path / "s.shape"))
    full_id = shape.save(profile, str(tmp_path / "f.shape"), capture="full")
    assert safe_id != full_id
    assert full_id == shape.save(profile, str(tmp_path / "f2.shape"), capture="full")


def test_saving_is_deterministic(profile: Any, tmp_path: Path) -> None:
    a = shape.save(profile, str(tmp_path / "a.shape"))
    b = shape.save(profile, str(tmp_path / "b.shape"))
    assert a == b


def test_a_loaded_safe_capture_saves_again_as_safe_and_never_as_full(
    profile: Any, tmp_path: Path
) -> None:
    first = tmp_path / "a.shape"
    shape.save(profile, str(first))
    loaded = shape.load(str(first))
    second = tmp_path / "b.shape"
    shape.save(loaded, str(second))
    assert shape.load(str(second)).to_dict() == loaded.to_dict()
    assert manifest_of(second)["redaction_manifest"] == manifest_of(first)["redaction_manifest"]
    with pytest.raises(ValueError, match="captured safe"):
        shape.save(loaded, str(tmp_path / "c.shape"), capture="full")


def test_a_loaded_full_capture_can_be_saved_safe(profile: Any, tmp_path: Path) -> None:
    full = tmp_path / "f.shape"
    shape.save(profile, str(full), capture="full")
    out = tmp_path / "s.shape"
    shape.save(shape.load(str(full)), str(out))
    assert manifest_of(out)["capture"]["mode"] == "safe"
    assert shape.load(str(out)).tables["table"]["columns"]["amount"]["max_value"] is None


# --- deliverable 4: the record, the version, the old files ----------------------------------------


def test_the_artifact_records_the_capture_and_the_manifest(profile: Any, tmp_path: Path) -> None:
    out = tmp_path / "p.shape"
    shape.save(profile, str(out))
    m = manifest_of(out)
    assert m["format"] == "shape" and m["version"] == 2 and m["format_version"] == 2
    assert m["capture"] == {"mode": "safe", "k": 5}
    cols = m["redaction_manifest"]["tables"]["table"]
    assert cols["email"]["suppressed"] and cols["grade"]["categories_dropped"] == 2


def test_a_full_capture_has_no_redaction_manifest(profile: Any, tmp_path: Path) -> None:
    out = tmp_path / "p.shape"
    shape.save(profile, str(out), capture="full")
    assert "redaction_manifest" not in manifest_of(out)


def test_the_profile_artifact_version_is_two() -> None:
    assert compat.KINDS["profile-artifact"].current == 2


def _v1_artifact(path: Path, profile: Any) -> Path:
    """A version 1 profile artifact, from the documented version 1 layout: no capture field."""
    body = codec.dumps(profile.to_dict(), sort_keys=False)
    manifest = {
        "format": "shape",
        "format_version": 1,
        "kind": "profile",
        "name": profile.name,
        "shape_content_id": sha256(body),
    }
    write_artifact(str(path), manifest, {"profile.json": body})
    return path


def test_a_version_1_artifact_still_loads_and_reads_as_full(profile: Any, tmp_path: Path) -> None:
    old = _v1_artifact(tmp_path / "old.shape", profile)
    loaded = shape.load(str(old))
    assert loaded.to_dict() == profile.to_dict()
    assert loaded.capture == {"mode": "full", "k": None}
    assert loaded.capture_declared is False
    assert loaded.redaction_manifest == {}


def test_a_version_2_artifact_without_a_capture_field_reads_as_full(
    profile: Any, tmp_path: Path
) -> None:
    body = codec.dumps(profile.to_dict(), sort_keys=False)
    manifest = compat.stamp(
        "profile-artifact",
        {"kind": "profile", "name": "x", "shape_content_id": sha256(body)},
    )
    out = tmp_path / "v2.shape"
    write_artifact(str(out), manifest, {"profile.json": body})
    assert shape.load(str(out)).capture["mode"] == "full"


def test_a_newer_version_names_the_release_that_reads_it(profile: Any, tmp_path: Path) -> None:
    out = tmp_path / "p.shape"
    shape.save(profile, str(out))
    manifest, parts = read_artifact(str(out))
    manifest = {**manifest, "version": 3, "format_version": 3, "min_shape_version": "9.9.9"}
    newer = tmp_path / "newer.shape"
    write_artifact(str(newer), manifest, parts)
    with pytest.raises(ArtifactError, match="9.9.9"):
        shape.load(str(newer))


def test_a_malformed_capture_field_is_refused(profile: Any, tmp_path: Path) -> None:
    out = tmp_path / "p.shape"
    shape.save(profile, str(out))
    manifest, parts = read_artifact(str(out))
    for bad in ({"mode": "partial", "k": 5}, {"mode": "safe", "k": 0}, "safe", {"k": 5}):
        broken = tmp_path / "broken.shape"
        broken.unlink(missing_ok=True)
        write_artifact(str(broken), {**manifest, "capture": bad}, parts)
        with pytest.raises(ArtifactError, match="capture"):
            shape.load(str(broken))


def test_unknown_fields_in_the_capture_record_are_kept_and_ignored(
    profile: Any, tmp_path: Path
) -> None:
    out = tmp_path / "p.shape"
    shape.save(profile, str(out))
    manifest, parts = read_artifact(str(out))
    extra = {**manifest, "capture": {**manifest["capture"], "x_note": "later release"}}
    newer = tmp_path / "x.shape"
    write_artifact(str(newer), extra, parts)
    assert shape.load(str(newer)).capture["mode"] == "safe"


def test_migrating_a_version_1_artifact_stamps_it_full(profile: Any, tmp_path: Path) -> None:
    from shape.migrate import migrate_file as migrate

    old = _v1_artifact(tmp_path / "old.shape", profile)
    before = old.read_bytes()
    result = migrate(old, tmp_path / "new.shape")
    assert old.read_bytes() == before  # the original is kept byte for byte
    assert result.plan.source_version == 1 and result.plan.target_version == 2
    assert result.plan.steps == ("profile-capture-full",)
    new = manifest_of(tmp_path / "new.shape")
    assert new["capture"] == {"mode": "full", "k": None} and new["migrated_from"] == 1
    assert shape.load(str(tmp_path / "new.shape")).to_dict() == profile.to_dict()
    again = migrate(tmp_path / "new.shape", tmp_path / "again.shape", dry_run=True)
    assert again.plan.noop  # a second run is a no-op
