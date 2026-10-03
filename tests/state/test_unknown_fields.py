"""Unknown optional fields are ignored on read and preserved on rewrite (W1-01, issue 55, item 6).

A newer release may add an optional field without a new version. This release must not fail on
it, must not drop it when it rewrites the file, and in strict mode must say it was there.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any

import pytest

import shape
from shape import compat
from shape.artifact.io import ArtifactError, canonical_json, read_artifact, write_container

EXTRA = {"x_future": {"nested": [1, 2, {"a": "b"}]}, "newer_optional": "kept"}


@pytest.fixture(scope="module")
def profile(tmp_path_factory: pytest.TempPathFactory) -> Any:
    path = tmp_path_factory.mktemp("unknown") / "orders.csv"
    lines = ["id,status,amount"] + [f"{i},{'a' if i % 2 else 'b'},{i * 1.5}" for i in range(30)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return shape.profile(str(path))


def _with_extra_manifest(path: Path, out: Path, extra: dict[str, Any]) -> Path:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        manifest, components = read_artifact(path, notice=False)
    write_container(out, canonical_json({**manifest, **extra}), dict(components))
    return out


def test_safe_profile_keeps_unknown_fields_at_every_level(profile: Any, tmp_path: Path) -> None:
    from shape.privacy.safe_profile import SafeProfile, to_safe_profile

    doc = to_safe_profile(profile).to_dict()
    doc.update({"top_new": 1, "x_top": {"k": [1]}})
    (table_name,) = doc["tables"]
    doc["tables"][table_name]["table_new"] = {"a": 1}
    column = next(iter(doc["tables"][table_name]["columns"]))
    doc["tables"][table_name]["columns"][column]["column_new"] = "kept"
    path = tmp_path / "newer.safe.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    loaded = SafeProfile.load(path)  # ignored: no error
    out = tmp_path / "again.safe.json"
    loaded.save(out)
    again = json.loads(out.read_text())
    assert again["top_new"] == 1 and again["x_top"] == {"k": [1]}
    assert again["tables"][table_name]["table_new"] == {"a": 1}
    assert again["tables"][table_name]["columns"][column]["column_new"] == "kept"
    assert again == json.loads(path.read_text())  # nothing else changed: byte-for-byte content


def test_safe_profile_strict_mode_names_the_unknown_field(profile: Any, tmp_path: Path) -> None:
    from shape.privacy.safe_profile import SafeProfile, to_safe_profile

    doc = to_safe_profile(profile).to_dict()
    doc["top_new"] = 1
    path = tmp_path / "s.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    SafeProfile.load(path)
    with compat.strict_formats(), pytest.raises(compat.FormatError, match="top_new"):
        SafeProfile.load(path)


def test_an_unknown_field_in_a_safe_profile_is_still_scanned_for_leaks(
    profile: Any, tmp_path: Path
) -> None:
    """Preserving a field must never launder a value: the leak scanner reads every field."""
    from shape.privacy.safe_profile import SafeProfile, to_safe_profile
    from shape.privacy.safe_validator import SafeProfileValidator

    doc = to_safe_profile(profile).to_dict()
    doc["smuggled"] = [f"user{i}@example.com" for i in range(40)]
    path = tmp_path / "leak.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    out = tmp_path / "leak-again.json"
    SafeProfile.load(path).save(out)
    assert not SafeProfileValidator().validate_file(out).is_clean


def test_artifact_manifest_unknown_fields_are_ignored_on_read(tmp_path: Path) -> None:
    from shape.artifact import read_model, write_model

    src = tmp_path / "a.shape"
    write_model(src, {"rows": 3, "columns": {}}, name="a")
    newer = _with_extra_manifest(src, tmp_path / "newer.shape", EXTRA)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        manifest, model = read_model(newer)
        _, reference = read_model(src)
    assert model == reference
    assert manifest["x_future"] == EXTRA["x_future"]  # visible to the caller, not dropped


def test_run_manifest_keeps_unknown_fields_on_rewrite(tmp_path: Path) -> None:
    from shape.scenario.manifest import ManifestBuilder

    b = ManifestBuilder()
    b.start(None, None, "retail", "small", 1)
    path = tmp_path / "run.json"
    ManifestBuilder.to_file(b.finish(), path)
    doc = json.loads(path.read_text())
    doc.update(EXTRA)
    path.write_text(json.dumps(doc), encoding="utf-8")

    rewritten = tmp_path / "run2.json"
    ManifestBuilder.to_file(ManifestBuilder.from_file(path), rewritten)
    out = json.loads(rewritten.read_text())
    assert out["x_future"] == EXTRA["x_future"] and out["newer_optional"] == "kept"
    with compat.strict_formats(), pytest.raises(compat.FormatError, match="newer_optional"):
        ManifestBuilder.from_file(path)


def test_generation_schema_keeps_x_fields(tmp_path: Path) -> None:
    from shape.generation.schema import GenSchema, GenSchemaError

    doc = {
        "schema_version": 1,
        "model": {"name": "m", "seed": 7},
        "tables": {
            "t": {
                "name": "t",
                "primary_key": ["k"],
                "columns": {
                    "k": {"name": "k", "type": "integer", "generator": {"strategy": "sequence"}}
                },
            }
        },
        "generation": {"scale": "s", "scales": {"s": {"t": 5}}},
        "x_owner": {"team": "data"},
    }
    schema = GenSchema.from_dict(doc)
    assert schema.to_dict()["x_owner"] == {"team": "data"}
    assert GenSchema.from_dict(schema.to_dict()).to_dict() == schema.to_dict()
    bad = {**doc, "unknown_top": 1}
    with pytest.raises(GenSchemaError, match="unknown_top"):
        GenSchema.from_dict(bad)  # hand-authored: a typo is still an error


def test_contract_model_keeps_unknown_fields() -> None:
    from shape.spec import FieldContract, ShapeContract

    c = ShapeContract("c", fields=(FieldContract("id", "integer", False),))
    doc = {**c.to_dict(), **EXTRA}
    again = ShapeContract.from_dict(doc)
    assert again == c
    assert again.to_dict()["x_future"] == EXTRA["x_future"]
    with compat.strict_formats(), pytest.raises(compat.FormatError, match="newer_optional"):
        ShapeContract.from_dict(doc)


def test_check_contract_accepts_x_fields_and_still_rejects_typos() -> None:
    from shape.contracts.v1 import ContractError, _validate_contract

    _validate_contract({"row_count": {"min": 1}, "x_note": "fine", "shape_version": "1.0.0"})
    with pytest.raises(ContractError, match="unknown contract keys"):
        _validate_contract({"row_count": {"min": 1}, "row_cuont": {}})


def test_verify_config_accepts_x_fields_and_still_rejects_typos() -> None:
    from shape.quality.verifyconfig import VerifyConfig, VerifyConfigError

    base = {"format": "shape-verify-config", "version": 1}
    VerifyConfig.from_dict({**base, "x_owner": "me", "shape_version": "1.0.0"})
    with pytest.raises(VerifyConfigError, match="unknown key"):
        VerifyConfig.from_dict({**base, "rangez": {}})


def test_pack_ignores_x_fields_silently_and_reports_the_rest() -> None:
    from shape.scenario.loader import PackLoader

    pack = PackLoader().parse(
        {"pack_version": 1, "id": "p", "kind": "file_drop", "x_owner": "me", "surprise": 1}
    )
    assert pack.extra_keys == ["surprise"]
    with compat.strict_formats(), pytest.raises(ValueError, match="surprise"):
        PackLoader().parse({"id": "p", "kind": "file_drop", "surprise": 1})


def test_signature_with_unknown_fields_still_verifies(tmp_path: Path) -> None:
    import zipfile

    from shape.artifact import signing, write_model

    key = b"\x03" * 32
    path = tmp_path / "s.shape"
    write_model(path, {"rows": 1, "columns": {}})
    signing.sign_artifact(path, key)
    with zipfile.ZipFile(path) as z:
        sig = json.loads(z.read("manifest.sig"))
        manifest_bytes = z.read("manifest.json")
    sig.update(EXTRA)
    signing.verify_manifest_signature(
        manifest_bytes, json.dumps(sig).encode(), signing.public_key_of(key)
    )
    with pytest.raises(ArtifactError):  # tampering with a known field still fails
        sig["signature"] = sig["signature"][::-1]
        signing.verify_manifest_signature(
            manifest_bytes, json.dumps(sig).encode(), signing.public_key_of(key)
        )


def test_registry_layout_marker_ignores_unknown_fields(tmp_path: Path) -> None:
    from shape.registry import LocalRegistry

    root = tmp_path / "r"
    LocalRegistry(root)
    marker = root / "layout.json"
    doc = json.loads(marker.read_text())
    doc.update(EXTRA)
    marker.write_text(json.dumps(doc), encoding="utf-8")
    assert LocalRegistry(root).layout_version == 1
