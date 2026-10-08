"""Items 4 (reference, git guard), 5 (verify) and 6 (rekey)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from shape.artifact.io import read_artifact
from shape.artifact.signing import generate_keypair, sign_artifact
from shape.vault import errors
from shape.vault.format import inspect_vault, open_vault
from shape.vault.kek import kek_id
from shape.vault.ops import (
    ensure_git_ignored,
    read_vault_bytes,
    rekey,
    sha256_hex,
    vault_reference,
    verify_vault,
)

PLAINTEXT_PARTS = ("paid", "planted.person@example.invalid", "ZQXRARE-CATEGORY-91", "987654321")


def _failed(report):
    return {c["check"] for c in report["checks"] if not c["ok"]}


def test_manifest_carries_vault_id_and_sha256(pair):
    shape_path, vault = pair
    manifest, _ = read_artifact(shape_path, notice=False)
    ref = vault_reference(manifest)
    raw = vault.read_bytes()
    assert ref == {"vault_id": inspect_vault(raw)["vault_id"], "sha256": sha256_hex(raw)}


def test_profile_without_vault_has_no_reference(profile_path):
    manifest, _ = read_artifact(profile_path, notice=False)
    assert "vault" not in manifest and vault_reference(manifest) is None


@pytest.mark.parametrize("bad", ["x", {"vault_id": "a"}, {"vault_id": "a" * 32, "sha256": "zz"}])
def test_malformed_reference_is_refused(bad):
    from shape.artifact.io import ArtifactError

    with pytest.raises(ArtifactError):
        vault_reference({"vault": bad})


def test_verify_ok_with_and_without_kek(pair, kek):
    shape_path, vault = pair
    assert verify_vault(vault, shape_path)["ok"]
    report = verify_vault(vault, shape_path, kek=kek)
    assert report["ok"] and {c["check"] for c in report["checks"]} >= {"sha256", "decrypt"}


def test_verify_wrong_kek_fails_naming_ids_not_keys(pair, other_kek, kek):
    shape_path, vault = pair
    report = verify_vault(vault, shape_path, kek=other_kek)
    assert _failed(report) == {"decrypt"}
    detail = next(c["detail"] for c in report["checks"] if c["check"] == "decrypt")
    assert kek_id(kek) in detail and kek_id(other_kek) in detail


def test_verify_vault_of_another_profile(pair, tmp_path, kek):
    from shape.vault.format import seal_vault
    from shape.vault.ops import write_file

    shape_path, vault = pair
    other = tmp_path / "other.shapevault"
    write_file(
        other, seal_vault({"t.c": ("categories", {"categories": [["x", 1]]})}, "cd" * 32, kek)
    )
    report = verify_vault(other, shape_path, kek=kek)
    assert {"sha256", "vault_id", "profile_content_id"} <= _failed(report)


def test_verify_profile_with_a_different_vault_hash(pair, tmp_path, kek):
    """Same profile id, but not the vault the profile records."""
    from shape.vault.format import seal_vault
    from shape.vault.ops import write_file

    shape_path, vault = pair
    manifest, _ = read_artifact(shape_path, notice=False)
    twin = tmp_path / "twin.shapevault"
    write_file(
        twin,
        seal_vault(
            {"t.c": ("categories", {"categories": [["x", 1]]})}, manifest["shape_content_id"], kek
        ),
    )
    report = verify_vault(twin, shape_path, kek=kek)
    assert {"sha256", "vault_id"} <= _failed(report) and not report["ok"]


def test_verify_without_reference_fails(profile_path, tmp_path, kek):
    from shape.vault.format import seal_vault
    from shape.vault.ops import write_file

    manifest, _ = read_artifact(profile_path, notice=False)
    v = tmp_path / "v.shapevault"
    write_file(v, seal_vault({}, manifest["shape_content_id"], kek))
    assert _failed(verify_vault(v, profile_path)) == {"reference"}


def test_any_changed_byte_of_the_vault_is_a_mismatch_not_a_crash(pair, kek):
    shape_path, vault = pair
    raw = vault.read_bytes()
    for i in range(0, len(raw), max(1, len(raw) // 97)):
        mutated = bytearray(raw)
        mutated[i] ^= 0x01
        vault.write_bytes(bytes(mutated))
        try:
            report = verify_vault(vault, shape_path, kek=kek)
        except errors.VaultError:
            pytest.fail(f"byte {i}: a changed vault must be a mismatch (exit 1), not an error")
        assert not report["ok"] and "sha256" in _failed(report)
    vault.write_bytes(raw)


def test_verify_truncated_vault_is_a_mismatch(pair):
    shape_path, vault = pair
    vault.write_bytes(vault.read_bytes()[:-40])
    assert "sha256" in _failed(verify_vault(vault, shape_path))


def test_malformed_vault_with_matching_hash_is_exit_2_class(profile_path, tmp_path):
    """The profile records the hash of garbage: the vault is malformed, not merely changed."""
    from shape.vault.ops import write_file

    junk = tmp_path / "junk.shapevault"
    write_file(junk, b"not a vault")
    with pytest.raises(errors.VaultFormatError):
        verify_vault(junk, profile_path)


def test_newer_version_names_minimum_release(pair, tmp_path):
    shape_path, vault = pair
    doc = json.loads(vault.read_bytes())
    doc["version"] = 2
    doc["min_shape_version"] = "7.7.0"
    newer = tmp_path / "newer.shapevault"
    newer.write_text(json.dumps(doc))
    with pytest.raises(errors.VaultVersionError, match="7.7.0"):
        verify_vault(newer, shape_path)


def test_verify_signature_covers_vault_hash(pair, tmp_path, kek):
    from shape.vault.format import seal_vault
    from shape.vault.ops import attach_vault

    shape_path, vault = pair
    sk, pk = generate_keypair()
    sign_artifact(shape_path, sk)
    assert verify_vault(vault, shape_path, kek=kek, verify_key=pk)["ok"]
    # replacing the vault reference invalidates the signature (it covers manifest.json)
    other_sk, other_pk = generate_keypair()
    assert "signature" in _failed(verify_vault(vault, shape_path, verify_key=other_pk))
    manifest, _ = read_artifact(shape_path, notice=False)
    forged = seal_vault({}, manifest["shape_content_id"], kek)
    tampered = tmp_path / "tampered.shape"
    shutil.copy(shape_path, tampered)
    attach_vault(tampered, forged)  # drops the signature
    assert "signature" in _failed(verify_vault(vault, tampered, verify_key=pk))


def test_verify_with_key_on_unsigned_profile_fails(pair):
    shape_path, vault = pair
    _, pk = generate_keypair()
    assert "signature" in _failed(verify_vault(vault, shape_path, verify_key=pk))


# --- rekey ------------------------------------------------------------------------------------


def _rekey(pair, tmp_path, kek, new_kek, **kw):
    shape_path, vault = pair
    return rekey(
        shape_path,
        vault,
        kek=kek,
        new_kek=new_kek,
        out_shape=tmp_path / "x2.shape",
        out_vault=tmp_path / "v2.shapevault",
        **kw,
    )


def test_rekey_writes_new_vault_and_profile(pair, tmp_path, kek, other_kek):
    shape_path, vault = pair
    before = (shape_path.read_bytes(), vault.read_bytes())
    result = _rekey(pair, tmp_path, kek, other_kek)
    assert (shape_path.read_bytes(), vault.read_bytes()) == before  # inputs untouched
    new_shape, new_vault = tmp_path / "x2.shape", tmp_path / "v2.shapevault"
    assert verify_vault(new_vault, new_shape, kek=other_kek)["ok"]
    # same values, new key id, new vault id, new nonces and ciphertexts
    old, new = json.loads(vault.read_bytes()), json.loads(new_vault.read_bytes())
    assert new["kek_id"] == kek_id(other_kek) != old["kek_id"]
    assert new["vault_id"] != old["vault_id"]
    for name in old["columns"]:
        assert new["columns"][name]["nonce"] != old["columns"][name]["nonce"]
        assert new["columns"][name]["ciphertext"] != old["columns"][name]["ciphertext"]
    a = open_vault(vault.read_bytes(), kek)
    b = open_vault(new_vault.read_bytes(), other_kek)
    assert {k: v.payload for k, v in a.columns.items()} == {
        k: v.payload for k, v in b.columns.items()
    }
    assert result["vault_id"] == new["vault_id"]
    # the old KEK no longer opens the new vault
    with pytest.raises(errors.KekMismatchError):
        open_vault(new_vault.read_bytes(), kek)


def test_rekey_profile_components_are_byte_identical(pair, tmp_path, kek, other_kek):
    shape_path, _ = pair
    _rekey(pair, tmp_path, kek, other_kek)
    m1, c1 = read_artifact(shape_path, notice=False)
    m2, c2 = read_artifact(tmp_path / "x2.shape", notice=False)
    assert c1 == c2 and m1["shape_content_id"] == m2["shape_content_id"]
    assert m1["vault"] != m2["vault"]


def test_rekey_drops_signature_with_a_notice(pair, tmp_path, kek, other_kek):
    shape_path, _ = pair
    sk, pk = generate_keypair()
    sign_artifact(shape_path, sk)
    result = _rekey(pair, tmp_path, kek, other_kek)
    assert any("signature" in n for n in result["notices"])
    read = read_artifact(tmp_path / "x2.shape", notice=False)
    assert read.signature["status"] == "unsigned"


def test_rekey_can_sign_again(pair, tmp_path, kek, other_kek):
    shape_path, _ = pair
    sk, pk = generate_keypair()
    result = _rekey(pair, tmp_path, kek, other_kek, signing_key=sk)
    assert result["notices"] == []
    assert verify_vault(tmp_path / "v2.shapevault", tmp_path / "x2.shape", verify_key=pk)["ok"]


def test_rekey_dry_run_lists_the_two_writes_and_writes_nothing(pair, tmp_path, kek, other_kek):
    result = _rekey(pair, tmp_path, kek, other_kek, dry_run=True)
    assert result["writes"] == [str(tmp_path / "v2.shapevault"), str(tmp_path / "x2.shape")]
    assert not (tmp_path / "v2.shapevault").exists() and not (tmp_path / "x2.shape").exists()


def test_rekey_with_the_wrong_old_kek_fails_and_writes_nothing(pair, tmp_path, other_kek):
    with pytest.raises(errors.VaultMismatchError):
        _rekey(pair, tmp_path, other_kek, other_kek)
    assert not (tmp_path / "v2.shapevault").exists() and not (tmp_path / "x2.shape").exists()


def test_rekey_refuses_to_work_in_place_or_overwrite(pair, tmp_path, kek, other_kek):
    shape_path, vault = pair
    with pytest.raises(errors.VaultInputError):
        rekey(
            shape_path,
            vault,
            kek=kek,
            new_kek=other_kek,
            out_shape=shape_path,
            out_vault=tmp_path / "n.shapevault",
        )
    with pytest.raises(errors.VaultInputError):
        rekey(
            shape_path,
            vault,
            kek=kek,
            new_kek=other_kek,
            out_shape=tmp_path / "n.shape",
            out_vault=vault,
        )
    (tmp_path / "x2.shape").write_text("keep")
    with pytest.raises(errors.VaultInputError):
        _rekey(pair, tmp_path, kek, other_kek)
    assert (tmp_path / "x2.shape").read_text() == "keep"


def test_rekey_refuses_a_vault_that_does_not_match_the_profile(pair, tmp_path, kek, other_kek):
    shape_path, vault = pair
    vault.write_bytes(vault.read_bytes() + b" ")
    with pytest.raises(errors.VaultReferenceError):
        _rekey(pair, tmp_path, kek, other_kek)


def test_no_plaintext_in_any_written_file(pair, tmp_path, kek, other_kek):
    _rekey(pair, tmp_path, kek, other_kek)
    for f in (pair[1], tmp_path / "v2.shapevault"):
        data = f.read_bytes()
        assert all(p.encode() not in data for p in PLAINTEXT_PARTS)


# --- git guard --------------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_vault_in_unignored_git_work_tree_is_refused_naming_gitignore_line(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    with pytest.raises(errors.VaultInputError, match=r"\*\.shapevault"):
        ensure_git_ignored([(tmp_path / "sub" / "x.shapevault", "vault")])
    (tmp_path / ".gitignore").write_text("*.shapevault\n")
    ensure_git_ignored([(tmp_path / "x.shapevault", "vault")])


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_kek_file_in_work_tree_is_refused_naming_its_name(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    with pytest.raises(errors.VaultInputError, match="kek.key"):
        ensure_git_ignored([(tmp_path / "kek.key", "key-encryption key file")])


def test_path_outside_any_work_tree_passes(tmp_path):
    ensure_git_ignored([(tmp_path / "x.shapevault", "vault")])


def test_read_vault_bytes_unreadable_is_input_error(tmp_path):
    with pytest.raises(errors.VaultInputError):
        read_vault_bytes(tmp_path / "missing.shapevault")


def test_vault_files_are_private(pair):
    import os
    import stat

    if os.name == "posix":
        assert stat.S_IMODE(Path(pair[1]).stat().st_mode) == 0o600
