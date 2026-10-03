"""The run manifest under the one compatibility policy, with the fields the reproducibility work
added (``reproducibility``, ``dataset_id``) and the error it raises for a newer version."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shape import compat
from shape.scenario.manifest import (
    MANIFEST_FORMAT,
    MANIFEST_VERSION,
    ManifestBuilder,
    ManifestVersionError,
)

DOC = {
    "run_id": "r",
    "pack_id": "p",
    "domain": "retail",
    "scale": "small",
    "seed": 1,
    "engine_version": "0.9.0",
    "reproducibility": {"seed": 1, "scale": "small"},
    "dataset_id": "sha256:abc",
}


def write(tmp_path: Path, doc: dict) -> Path:
    path = tmp_path / "m.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_the_identifier_is_the_one_in_the_compatibility_table() -> None:
    kind = compat.KINDS["run-manifest"]
    assert (MANIFEST_FORMAT, MANIFEST_VERSION) == (kind.format, kind.current)


def test_the_two_key_form_without_writer_keys_reads(tmp_path: Path) -> None:
    doc = {**DOC, "format": MANIFEST_FORMAT, "version": 1}  # no shape_version / min_shape_version
    m = ManifestBuilder.from_file(write(tmp_path, doc))
    assert m.dataset_id == "sha256:abc" and m.reproducibility["seed"] == 1
    out = m.to_dict()
    assert out["format"] == MANIFEST_FORMAT and out["version"] == 1
    assert out["shape_version"] and out["min_shape_version"]


def test_a_manifest_without_a_declaration_reads_with_empty_new_fields(tmp_path: Path) -> None:
    m = ManifestBuilder.from_file(write(tmp_path, {"run_id": "old", "seed": 3}))
    assert m.dataset_id == "" and m.reproducibility == {}


def test_a_newer_version_raises_both_errors_and_names_the_release(tmp_path: Path) -> None:
    doc = {**DOC, "format": MANIFEST_FORMAT, "version": 2, "min_shape_version": "3.1.0"}
    with pytest.raises(ManifestVersionError, match="3.1.0") as caught:
        ManifestBuilder.from_file(write(tmp_path, doc))
    assert isinstance(caught.value, compat.UnsupportedVersionError)
    assert isinstance(caught.value, ValueError)
    assert caught.value.min_shape_version == "3.1.0" and caught.value.found == 2


def test_another_format_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a run manifest"):
        ManifestBuilder.from_file(write(tmp_path, {"format": "shape-contract", "version": 1}))


def test_unknown_fields_survive_a_rewrite(tmp_path: Path) -> None:
    doc = {**DOC, "format": MANIFEST_FORMAT, "version": 1, "x_note": {"a": 1}}
    out = ManifestBuilder.from_file(write(tmp_path, doc)).to_dict()
    assert out["x_note"] == {"a": 1} and out["dataset_id"] == "sha256:abc"
