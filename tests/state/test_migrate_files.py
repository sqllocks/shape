"""Offline migration of persisted files (W1-01, issue 55, item 4 and 7).

A migration never rewrites in place: it writes a new file and keeps the original, records
``migrated_from`` and ``source_content_id``, has a dry run, refuses downgrades and checks its own
result through the content id. A signed source keeps its signature as evidence: the original file
is untouched and a signed receipt says what was done.
"""

from __future__ import annotations

import hashlib
import json
import os
import warnings
import zipfile
from pathlib import Path
from typing import Any

import pytest
from shape.migrate import MigrationError

import shape
from shape import compat, migrate
from shape.artifact import codec, read_model, signing, write_model
from shape.artifact.io import (
    ArtifactSignatureError,
    canonical_json,
    read_artifact,
    sha256,
    write_artifact,
    write_container,
)

OLD_KEY = b"\x04" * 32
NEW_KEY = b"\x05" * 32


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree(path: Path) -> dict[str, str]:
    return {p.name: sha_file(p) for p in sorted(path.iterdir()) if p.is_file()}


@pytest.fixture(scope="module")
def csv(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("migrate") / "orders.csv"
    lines = ["id,status,amount"] + [f"{i},{'a' if i % 2 else 'b'},{i * 1.5}" for i in range(30)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def profile(csv: Path) -> Any:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return shape.profile(str(csv))


def capture_doc() -> dict[str, Any]:
    from shape.capture import capture_rows

    cap = capture_rows([{"id": i, "v": i * 2} for i in range(12)])
    doc = cap.to_dict() if hasattr(cap, "to_dict") else cap
    assert isinstance(doc, dict)
    return doc


def make_v1(path: Path, extra: dict[str, Any] | None = None) -> Path:
    """A version 1 artifact, from the documented version 1 layout (nothing writes it any more)."""
    body = codec.dumps(capture_doc(), sort_keys=True)
    manifest = {
        "format": "shape",
        "format_version": 1,
        "name": "orders",
        "shape_content_id": sha256(body),
        "fidelity": "silver",
        "classification": "INTERNAL",
        "metadata": {"owner": "data"},
        **(extra or {}),
    }
    write_artifact(path, manifest, {"shape.json": body})
    return path


def make_v2(path: Path) -> Path:
    write_model(path, capture_doc(), name="orders", fidelity="silver", classification="INTERNAL")
    return path


def downgrade_keys(path: Path, out: Path) -> Path:
    """The artifact as a release before the unified keys wrote it: ``format_version`` only."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        manifest, components = read_artifact(path, notice=False)
    manifest = {
        k: v
        for k, v in manifest.items()
        if k not in ("version", "shape_version", "min_shape_version")
    }
    write_container(out, canonical_json(manifest), dict(components))
    return out


def manifest_of(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as z:
        doc = json.loads(z.read("manifest.json"))
    assert isinstance(doc, dict)
    return doc


def body_of(path: Path) -> bytes:
    with zipfile.ZipFile(path) as z:
        return z.read("shape.json")


# --- dry run and the plan ------------------------------------------------------------------------


def test_dry_run_writes_nothing_and_reports_the_plan(tmp_path: Path) -> None:
    src = make_v1(tmp_path / "a.shape")
    before = tree(tmp_path)
    result = migrate.migrate_file(src, tmp_path / "b.shape", dry_run=True)
    assert tree(tmp_path) == before and not (tmp_path / "b.shape").exists()
    assert result.dry_run and not result.written
    p = result.plan
    assert (p.kind, p.source_version, p.target_version) == ("artifact", 1, 2)
    assert p.steps == ("capture-v1-to-model-v2",)
    assert p.source_content_id == manifest_of(src)["shape_content_id"]
    assert not p.noop


def test_plan_predicts_the_content_id_the_migration_writes(tmp_path: Path) -> None:
    src = make_v1(tmp_path / "a.shape")
    planned = migrate.plan(src).result_content_id
    migrate.migrate_file(src, tmp_path / "b.shape")
    assert manifest_of(tmp_path / "b.shape")["shape_content_id"] == planned


# --- version 1 -> 2 ------------------------------------------------------------------------------


def test_migrating_writes_a_new_file_and_keeps_the_original(tmp_path: Path) -> None:
    src = make_v1(tmp_path / "a.shape")
    original = src.read_bytes()
    dst = tmp_path / "b.shape"
    result = migrate.migrate_file(src, dst)
    assert src.read_bytes() == original, "the original is kept byte for byte"
    assert result.written and dst.exists()
    m = manifest_of(dst)
    assert m["version"] == 2 and m["format_version"] == 2
    assert m["migrated_from"] == 1
    assert m["source_content_id"] == manifest_of(src)["shape_content_id"]
    assert m["shape_version"] == shape.__version__
    assert (m["name"], m["fidelity"], m["classification"]) == ("orders", "silver", "INTERNAL")
    assert m["metadata"] == {"owner": "data"}


def test_the_migrated_file_reads_like_the_source_read_through_the_registry(tmp_path: Path) -> None:
    src = make_v1(tmp_path / "a.shape")
    dst = tmp_path / "b.shape"
    migrate.migrate_file(src, dst)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m_src, model_src = read_model(src)
        m_dst, model_dst = read_model(dst)
    assert codec.dumps(model_src, sort_keys=True) == codec.dumps(model_dst, sort_keys=True)
    assert m_dst["shape_content_id"] == m_src["shape_content_id"]
    assert sha256(body_of(dst)) == m_dst["shape_content_id"], "round trip through the content id"


def test_a_migration_is_idempotent_and_deterministic(tmp_path: Path) -> None:
    src = make_v1(tmp_path / "a.shape")
    migrate.migrate_file(src, tmp_path / "b.shape")
    migrate.migrate_file(src, tmp_path / "c.shape")
    assert (tmp_path / "b.shape").read_bytes() == (tmp_path / "c.shape").read_bytes()
    again = migrate.migrate_file(tmp_path / "b.shape", tmp_path / "d.shape")
    assert again.plan.noop and not again.written and not (tmp_path / "d.shape").exists()


def test_unknown_manifest_fields_are_preserved(tmp_path: Path) -> None:
    src = make_v1(tmp_path / "a.shape", {"x_audit": {"by": "ci"}, "newer_optional": [1, 2]})
    migrate.migrate_file(src, tmp_path / "b.shape")
    m = manifest_of(tmp_path / "b.shape")
    assert m["x_audit"] == {"by": "ci"} and m["newer_optional"] == [1, 2]


def test_the_manifest_hashes_of_the_result_are_valid(tmp_path: Path) -> None:
    src = make_v1(tmp_path / "a.shape")
    migrate.migrate_file(src, tmp_path / "b.shape")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        read_artifact(tmp_path / "b.shape", notice=False)  # raises on any hash mismatch


# --- key names only ------------------------------------------------------------------------------


def test_an_old_key_name_artifact_gains_the_unified_keys_with_its_content_unchanged(
    tmp_path: Path,
) -> None:
    old = downgrade_keys(make_v2(tmp_path / "v2.shape"), tmp_path / "old.shape")
    assert "version" not in manifest_of(old)
    dst = tmp_path / "new.shape"
    result = migrate.migrate_file(old, dst)
    assert result.plan.source_version == result.plan.target_version == 2
    assert result.plan.steps == ("unify-version-keys",)
    m = manifest_of(dst)
    assert m["version"] == 2 and m["shape_version"] == shape.__version__
    assert body_of(dst) == body_of(old), "the content, and so its content id, is unchanged"
    assert m["shape_content_id"] == manifest_of(old)["shape_content_id"]
    assert m["migrated_from"] == 2 and m["source_content_id"] == m["shape_content_id"]


def test_a_current_artifact_is_a_no_op(tmp_path: Path) -> None:
    src = make_v2(tmp_path / "a.shape")
    result = migrate.migrate_file(src, tmp_path / "b.shape")
    assert result.plan.noop and not result.written
    assert not (tmp_path / "b.shape").exists()


# --- the four refusals ---------------------------------------------------------------------------


def test_in_place_is_refused(tmp_path: Path) -> None:
    src = make_v1(tmp_path / "a.shape")
    original = src.read_bytes()
    with pytest.raises(MigrationError, match="in place"):
        migrate.migrate_file(src, src)
    with pytest.raises(MigrationError, match="in place"):
        migrate.migrate_file(src, tmp_path / "." / "a.shape")
    assert src.read_bytes() == original


@pytest.mark.skipif(os.name == "nt", reason="links need privileges on Windows")
def test_in_place_through_a_link_is_refused(tmp_path: Path) -> None:
    src = make_v1(tmp_path / "a.shape")
    (tmp_path / "soft.shape").symlink_to(src)
    os.link(src, tmp_path / "hard.shape")
    for name in ("soft.shape", "hard.shape"):
        with pytest.raises(MigrationError, match="in place|exists"):
            migrate.migrate_file(src, tmp_path / name)


def test_an_existing_destination_is_never_overwritten(tmp_path: Path) -> None:
    src = make_v1(tmp_path / "a.shape")
    dst = tmp_path / "b.shape"
    dst.write_bytes(b"precious")
    with pytest.raises(MigrationError, match="exists"):
        migrate.migrate_file(src, dst)
    assert dst.read_bytes() == b"precious"


def test_downgrades_are_refused(tmp_path: Path) -> None:
    src = make_v2(tmp_path / "a.shape")
    with pytest.raises(MigrationError, match="downgrade"):
        migrate.migrate_file(src, tmp_path / "b.shape", to=1)
    assert not (tmp_path / "b.shape").exists()


def test_a_target_beyond_what_this_release_writes_is_refused(tmp_path: Path) -> None:
    src = make_v1(tmp_path / "a.shape")
    with pytest.raises(MigrationError, match="no version 3"):
        migrate.migrate_file(src, tmp_path / "b.shape", to=3)


def test_a_newer_source_is_refused_with_the_minimum_release(tmp_path: Path) -> None:
    src = make_v2(tmp_path / "a.shape")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        manifest, components = read_artifact(src, notice=False)
    newer = tmp_path / "newer.shape"
    write_container(
        newer,
        canonical_json(
            {**manifest, "version": 3, "format_version": 3, "min_shape_version": "9.9.0"}
        ),
        dict(components),
    )
    with pytest.raises(compat.UnsupportedVersionError, match=r"Shape 9\.9\.0 or newer"):
        migrate.migrate_file(newer, tmp_path / "b.shape")


def test_a_damaged_source_is_refused_and_nothing_is_written(tmp_path: Path) -> None:
    src = make_v1(tmp_path / "a.shape")
    data = bytearray(src.read_bytes())
    data[len(data) // 2] ^= 0xFF
    src.write_bytes(bytes(data))
    with pytest.raises(Exception):  # noqa: B017  (any ArtifactError / BadZipFile)
        migrate.migrate_file(src, tmp_path / "b.shape")
    assert not (tmp_path / "b.shape").exists()
    assert not list(tmp_path.glob("*.receipt.json"))


def test_a_failed_check_leaves_no_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = make_v1(tmp_path / "a.shape")

    def broken(*args: Any, **kwargs: Any) -> None:
        raise MigrationError("round trip check failed")

    monkeypatch.setattr(migrate, "_check_round_trip", broken)
    with pytest.raises(MigrationError, match="round trip"):
        migrate.migrate_file(src, tmp_path / "b.shape")
    assert not (tmp_path / "b.shape").exists() and not list(tmp_path.glob("*.receipt.json"))


# --- the receipt ---------------------------------------------------------------------------------


def test_a_receipt_records_what_was_done(tmp_path: Path) -> None:
    src = make_v1(tmp_path / "a.shape")
    dst = tmp_path / "b.shape"
    result = migrate.migrate_file(src, dst)
    receipt = json.loads(result.receipt.read_text())
    assert receipt["format"] == "shape-migration-receipt" and receipt["version"] == 1
    assert receipt["shape_version"] == shape.__version__
    compat.parse_utc_iso(receipt["created"])
    assert receipt["kind"] == "artifact"
    assert receipt["source"]["file_sha256"] == sha_file(src)
    assert receipt["source"]["content_id"] == manifest_of(src)["shape_content_id"]
    assert receipt["source"]["version"] == 1 and receipt["result"]["version"] == 2
    assert receipt["result"]["file_sha256"] == sha_file(dst)
    assert receipt["steps"] == ["capture-v1-to-model-v2"]
    assert receipt["source"]["signature"] is None and "signature" not in receipt


# --- signed sources ------------------------------------------------------------------------------


def signed_v1(tmp_path: Path) -> Path:
    src = make_v1(tmp_path / "signed.shape")
    signing.sign_artifact(src, OLD_KEY)
    return src


def test_a_signed_source_needs_a_key_for_the_receipt(tmp_path: Path) -> None:
    src = signed_v1(tmp_path)
    with pytest.raises(MigrationError, match="--sign-key"):
        migrate.migrate_file(src, tmp_path / "b.shape")
    assert not (tmp_path / "b.shape").exists()


def test_a_signed_source_is_kept_as_evidence_and_the_receipt_is_signed(tmp_path: Path) -> None:
    src = signed_v1(tmp_path)
    original = src.read_bytes()
    dst = tmp_path / "b.shape"
    old_pub = signing.public_key_of(OLD_KEY)
    result = migrate.migrate_file(src, dst, sign_key=NEW_KEY, verify_key=old_pub)
    assert src.read_bytes() == original
    signing.verify_artifact(src, old_pub)  # the original signature still verifies
    signing.verify_artifact(dst, signing.public_key_of(NEW_KEY))  # the new file is signed
    receipt = json.loads(result.receipt.read_text())
    assert receipt["source"]["signature"] == {
        "algorithm": "Ed25519",
        "key_id": signing.key_id(old_pub),
        "verified": True,
    }
    assert receipt["signature"]["algorithm"] == "Ed25519"
    assert receipt["signature"]["key_id"] == signing.key_id(signing.public_key_of(NEW_KEY))
    migrate.verify_receipt(result.receipt, signing.public_key_of(NEW_KEY), source=src, result=dst)


def test_the_receipt_signature_covers_every_field(tmp_path: Path) -> None:
    src = signed_v1(tmp_path)
    result = migrate.migrate_file(src, tmp_path / "b.shape", sign_key=NEW_KEY)
    pub = signing.public_key_of(NEW_KEY)
    doc = json.loads(result.receipt.read_text())
    doc["source"]["content_id"] = "0" * 64
    forged = tmp_path / "forged.receipt.json"
    forged.write_text(json.dumps(doc))
    with pytest.raises(ArtifactSignatureError):
        migrate.verify_receipt(forged, pub)
    with pytest.raises(ArtifactSignatureError):  # another key
        migrate.verify_receipt(result.receipt, signing.public_key_of(OLD_KEY))


def test_an_unsigned_receipt_is_refused_by_the_verifier(tmp_path: Path) -> None:
    result = migrate.migrate_file(make_v1(tmp_path / "a.shape"), tmp_path / "b.shape")
    with pytest.raises(ArtifactSignatureError, match="not signed"):
        migrate.verify_receipt(result.receipt, signing.public_key_of(NEW_KEY))


def test_the_receipt_checks_the_files_it_names(tmp_path: Path) -> None:
    src = signed_v1(tmp_path)
    dst = tmp_path / "b.shape"
    result = migrate.migrate_file(src, dst, sign_key=NEW_KEY)
    pub = signing.public_key_of(NEW_KEY)
    migrate.verify_receipt(result.receipt, pub, source=src, result=dst)
    other = make_v2(tmp_path / "other.shape")
    with pytest.raises(MigrationError, match="result"):
        migrate.verify_receipt(result.receipt, pub, source=src, result=other)
    with pytest.raises(MigrationError, match="source"):
        migrate.verify_receipt(result.receipt, pub, source=other, result=dst)


def test_an_unsigned_receipt_can_be_accepted_explicitly(tmp_path: Path) -> None:
    src = signed_v1(tmp_path)
    result = migrate.migrate_file(src, tmp_path / "b.shape", unsigned_receipt=True)
    receipt = json.loads(result.receipt.read_text())
    assert "signature" not in receipt
    assert receipt["source"]["signature"]["verified"] is False
    assert receipt["source"]["signature"]["key_id"] == signing.key_id(
        signing.public_key_of(OLD_KEY)
    )
    assert not manifest_has_signature(tmp_path / "b.shape")


def manifest_has_signature(path: Path) -> bool:
    with zipfile.ZipFile(path) as z:
        return "manifest.sig" in z.namelist()


def test_the_wrong_verify_key_stops_the_migration(tmp_path: Path) -> None:
    src = signed_v1(tmp_path)
    with pytest.raises(ArtifactSignatureError):
        migrate.migrate_file(
            src, tmp_path / "b.shape", sign_key=NEW_KEY, verify_key=signing.public_key_of(NEW_KEY)
        )
    assert not (tmp_path / "b.shape").exists() and not list(tmp_path.glob("*.receipt.json"))


def test_rotation_the_old_key_verifies_what_it_signed_after_the_new_key_took_over(
    tmp_path: Path,
) -> None:
    """Key rotation and retired keys (docs/SIGNING.md): a retired key's public half stays in the
    trust list for what it signed; every signature names its algorithm and key."""
    old = signed_v1(tmp_path)
    new = make_v1(tmp_path / "new.shape")
    signing.sign_artifact(new, NEW_KEY)
    old_pub, new_pub = signing.public_key_of(OLD_KEY), signing.public_key_of(NEW_KEY)
    signing.verify_artifact(old, old_pub)
    signing.verify_artifact(new, new_pub)
    with pytest.raises(ArtifactSignatureError, match="different key"):
        signing.verify_artifact(old, new_pub)
    for path, pub in ((old, old_pub), (new, new_pub)):
        with zipfile.ZipFile(path) as z:
            sig = json.loads(z.read("manifest.sig"))
        assert sig["algorithm"] == "Ed25519" and sig["key_id"] == signing.key_id(pub)
        assert sig["format"] == "shape-signature" and sig["version"] == 1


# --- JSON kinds ----------------------------------------------------------------------------------


def test_a_safe_profile_gains_the_unified_keys_keeping_unknown_fields(
    profile: Any, tmp_path: Path
) -> None:
    from shape.privacy.safe_profile import SafeProfile, to_safe_profile

    doc = to_safe_profile(profile).to_dict()
    for key in ("format", "version", "shape_version", "min_shape_version"):
        doc.pop(key)
    doc["x_future"] = {"k": 1}
    src = tmp_path / "old.safe.json"
    src.write_text(json.dumps(doc), encoding="utf-8")
    dst = tmp_path / "new.safe.json"
    result = migrate.migrate_file(src, dst)
    assert result.plan.kind == "safe-profile" and result.plan.steps == ("unify-version-keys",)
    out = json.loads(dst.read_text())
    assert out["format"] == "shape-safe-profile" and out["version"] == 1
    assert out["schema_version"] == 1 and out["x_future"] == {"k": 1}
    assert out["migrated_from"] == 1 and out["source_content_id"] == result.plan.source_content_id
    assert SafeProfile.load(dst).to_dict()["tables"] == SafeProfile.load(src).to_dict()["tables"]
    assert migrate.plan(dst).noop


def test_an_engine_document_migrates_to_a_model_v2_file(csv: Path, tmp_path: Path) -> None:
    from shape.profile.engine import profile_many
    from shape.spec.migrate import to_model

    engine = profile_many({"orders": str(csv)})
    src = tmp_path / "engine.json"
    src.write_bytes(codec.dumps(engine, sort_keys=True))
    result = migrate.migrate_file(src, tmp_path / "model.json")
    assert (result.plan.kind, result.plan.source_version, result.plan.target_version) == (
        "model",
        1,
        2,
    )
    out = codec.loads((tmp_path / "model.json").read_bytes())
    assert out == to_model(engine)
    receipt = json.loads(result.receipt.read_text())
    assert receipt["source"]["version"] == 1 and receipt["result"]["version"] == 2
    assert receipt["steps"] == ["engine-v1-to-model-v2"]
    assert migrate.plan(tmp_path / "model.json").noop


def test_a_run_manifest_and_a_contract_gain_the_unified_keys(tmp_path: Path) -> None:
    run = tmp_path / "run.json"
    run.write_text(json.dumps({"run_id": "r", "spec_hash": "", "pack_id": "p", "seed": 1}))
    result = migrate.migrate_file(run, tmp_path / "run2.json")
    assert result.plan.kind == "run-manifest"
    out = json.loads((tmp_path / "run2.json").read_text())
    assert out["format"] == "shape-run-manifest" and out["run_id"] == "r"

    contract = tmp_path / "c.json"
    contract.write_text(json.dumps({"name": "customer", "version": 1, "fields": []}))
    result = migrate.migrate_file(contract, tmp_path / "c2.json")
    assert result.plan.kind == "contract-model"
    assert json.loads((tmp_path / "c2.json").read_text())["format"] == "shape-contract-model"


def test_a_json_file_of_an_unknown_kind_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    path.write_text('{"hello": "world"}')
    with pytest.raises(MigrationError, match="not a file kind"):
        migrate.migrate_file(path, tmp_path / "y.json")
    with pytest.raises(MigrationError, match="not a file kind"):
        migrate.migrate_file(tmp_path / "x.json", tmp_path / "y.json", kind="nonsense")


def test_an_explicit_kind_overrides_the_sniff(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"row_count": {"min": 1}, "columns": {}}))
    result = migrate.migrate_file(path, tmp_path / "c2.json", kind="contract")
    assert result.plan.kind == "contract"
    assert json.loads((tmp_path / "c2.json").read_text())["format"] == "shape-contract"


# --- command line --------------------------------------------------------------------------------


def test_the_command_has_a_dry_run_and_exit_codes(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    from shape.cli import migrate as cli

    src = make_v1(tmp_path / "a.shape")
    assert cli.main([str(src), str(tmp_path / "b.shape"), "--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True and out["plan"]["target_version"] == 2
    assert not (tmp_path / "b.shape").exists()
    assert cli.main([str(src), str(tmp_path / "b.shape")]) == 0
    assert (tmp_path / "b.shape").exists()
    capsys.readouterr()
    assert cli.main([str(src), str(tmp_path / "b.shape")]) == 2  # never overwrites
    assert "exists" in capsys.readouterr().err
    assert cli.main([str(src), str(src)]) == 2
    assert cli.main([str(tmp_path / "b.shape"), str(tmp_path / "c.shape"), "--to", "1"]) == 2


def test_shape_migrate_is_routed_and_installed_as_a_command() -> None:
    import tomllib

    root = Path(__file__).resolve().parents[2]
    scripts = tomllib.loads((root / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts["shape-migrate"] == "shape.cli.migrate:main_entry"


def test_shape_migrate_help_through_the_shape_command(capsys: pytest.CaptureFixture) -> None:
    from shape.cli.main import main

    with pytest.raises(SystemExit) as e:
        main(["migrate", "--help"])
    assert e.value.code == 0
    assert "--dry-run" in capsys.readouterr().out
