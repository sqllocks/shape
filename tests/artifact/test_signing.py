"""P7-03 / P19: artifact signing. A forged artifact with rewritten hashes fails verification."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import zipfile

import pytest

from shape.artifact import (
    ArtifactSignatureError,
    read_model,
    read_shape,
    sign_artifact,
    verify_artifact,
    write_model,
)
from shape.artifact.io import ArtifactError, canonical_json, read_artifact, write_artifact
from shape.artifact.keys import UnencryptedKeyWarning
from shape.artifact.signing import (
    generate_keypair,
    key_id,
    load_private_key,
    load_public_key,
    write_keypair,
)

pytestmark = pytest.mark.sign  # needs the optional cryptography package ([sign])

MODEL = {"columns": {"a": {"dtype": "int64"}}}


def _make(tmp_path, name="a.shape"):
    p = tmp_path / name
    write_model(p, {"tables": {}}, name="t")
    return p


def _members(p):
    with zipfile.ZipFile(p) as z:
        return {i.filename: z.read(i.filename) for i in z.infolist()}


def _rewrite(p, members):
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        for k, v in members.items():
            z.writestr(k, v)


def test_sign_then_verify_round_trip(tmp_path):
    sk, pk = generate_keypair()
    p = _make(tmp_path)
    assert sign_artifact(p, sk) == key_id(pk)
    assert verify_artifact(p, pk) == {"verified": True, "key_id": key_id(pk)}
    # the signed file still reads normally, with or without the key
    assert read_model(p)[1] == read_model(p, verify_key=pk)[1]
    read_shape(p, verify_key=pk)
    read_artifact(p)


def test_unsigned_artifact_fails_verify(tmp_path):
    _, pk = generate_keypair()
    p = _make(tmp_path)
    with pytest.raises(ArtifactSignatureError, match="not signed"):
        verify_artifact(p, pk)


def test_wrong_key_fails(tmp_path):
    sk, _ = generate_keypair()
    _, other = generate_keypair()
    p = _make(tmp_path)
    sign_artifact(p, sk)
    with pytest.raises(ArtifactSignatureError):
        verify_artifact(p, other)


def test_forged_artifact_with_rewritten_hashes_fails_verify(tmp_path):
    """P19: tamper with the model, then rewrite shape_content_id and content_hashes so every
    checksum is consistent. The plain reader accepts it; verification does not."""
    sk, pk = generate_keypair()
    p = _make(tmp_path)
    sign_artifact(p, sk)
    members = _members(p)
    manifest = json.loads(members["manifest.json"])
    model = json.loads(members["shape.json"])
    model["name"] = "forged"
    body = json.dumps(model, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(body).hexdigest()
    manifest["shape_content_id"] = digest
    manifest["content_hashes"]["shape.json"] = digest
    members["manifest.json"] = canonical_json(manifest)
    members["shape.json"] = body
    forged = tmp_path / "forged.shape"
    _rewrite(forged, members)  # keeps the original manifest.sig
    read_artifact(forged)  # checksum-only authenticity is satisfied: this is the P19 hole
    with pytest.raises(ArtifactSignatureError):
        verify_artifact(forged, pk)
    with pytest.raises(ArtifactSignatureError):
        read_model(forged, verify_key=pk)


def test_forged_and_stripped_signature_fails(tmp_path):
    sk, pk = generate_keypair()
    p = _make(tmp_path)
    sign_artifact(p, sk)
    members = _members(p)
    del members["manifest.sig"]
    stripped = tmp_path / "stripped.shape"
    _rewrite(stripped, members)
    with pytest.raises(ArtifactSignatureError, match="not signed"):
        verify_artifact(stripped, pk)


def test_attacker_resigns_with_own_key_fails_trusted_key(tmp_path):
    sk, pk = generate_keypair()
    attacker_sk, _ = generate_keypair()
    p = _make(tmp_path)
    sign_artifact(p, attacker_sk)
    with pytest.raises(ArtifactSignatureError):
        verify_artifact(p, pk)
    # even claiming the trusted key id does not help: the signature itself is checked
    members = _members(p)
    sig = json.loads(members["manifest.sig"])
    sig["key_id"] = key_id(pk)
    members["manifest.sig"] = json.dumps(sig).encode()
    claimed = tmp_path / "claimed.shape"
    _rewrite(claimed, members)
    with pytest.raises(ArtifactSignatureError):
        verify_artifact(claimed, pk)


def test_signature_is_not_valid_for_another_message(tmp_path):
    # a bare signature of the manifest (no domain prefix) is rejected
    from shape.security.crypto import sign_ed25519

    sk, pk = generate_keypair()
    p = _make(tmp_path)
    members = _members(p)
    import base64

    members["manifest.sig"] = json.dumps(
        {
            "algorithm": "Ed25519",
            "key_id": key_id(pk),
            "signature": base64.b64encode(sign_ed25519(members["manifest.json"], sk)).decode(),
        }
    ).encode()
    q = tmp_path / "raw.shape"
    _rewrite(q, members)
    with pytest.raises(ArtifactSignatureError):
        verify_artifact(q, pk)


@pytest.mark.parametrize(
    "junk", [b"", b"not json", b"[]", b'{"algorithm":"RSA"}', b'{"algorithm":"Ed25519"}']
)
def test_malformed_signature_member_fails(tmp_path, junk):
    _, pk = generate_keypair()
    p = _make(tmp_path)
    members = _members(p)
    members["manifest.sig"] = junk
    q = tmp_path / "bad.shape"
    _rewrite(q, members)
    with pytest.raises(ArtifactSignatureError):
        verify_artifact(q, pk)


def test_resign_replaces_signature(tmp_path):
    sk1, pk1 = generate_keypair()
    sk2, pk2 = generate_keypair()
    p = _make(tmp_path)
    sign_artifact(p, sk1)
    sign_artifact(p, sk2)
    verify_artifact(p, pk2)
    with pytest.raises(ArtifactSignatureError):
        verify_artifact(p, pk1)
    assert list(_members(p)).count("manifest.sig") == 1


def test_sign_to_output_leaves_source_untouched(tmp_path):
    sk, pk = generate_keypair()
    p = _make(tmp_path)
    out = tmp_path / "signed.shape"
    sign_artifact(p, sk, out)
    verify_artifact(out, pk)
    with pytest.raises(ArtifactSignatureError):
        verify_artifact(p, pk)


def test_signature_member_is_reserved_for_writers(tmp_path):
    with pytest.raises(ArtifactError):
        write_artifact(tmp_path / "x.shape", {"format": "shape"}, {"manifest.sig": b"x"})


def test_damaged_artifact_is_not_signed(tmp_path):
    sk, _ = generate_keypair()
    p = _make(tmp_path)
    members = _members(p)
    members["shape.json"] = members["shape.json"] + b" "
    _rewrite(p, members)
    with pytest.raises(ArtifactError):
        sign_artifact(p, sk)


def test_key_files(tmp_path):
    prefix = tmp_path / "k"
    # the raw (unencrypted) form still works when asked for, with a warning (issue #38)
    with pytest.warns(UnencryptedKeyWarning):
        priv, pub = write_keypair(prefix, unencrypted=True)
    if os.name == "posix":
        assert stat.S_IMODE(priv.stat().st_mode) == 0o600
    sk, pk = load_private_key(priv), load_public_key(pub)
    p = _make(tmp_path)
    sign_artifact(p, sk)
    verify_artifact(p, pk)
    with pytest.raises(FileExistsError):  # never overwrites a key
        write_keypair(prefix, unencrypted=True)
    bad = tmp_path / "bad.pub"
    bad.write_text("AAAA\n")
    with pytest.raises(ValueError):
        load_public_key(bad)


def _cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "shape.cli.main", *args], capture_output=True, text=True
    )


def test_cli_sign_verify_flags(tmp_path):
    prefix = tmp_path / "k"
    r = _cli("keygen", str(prefix), "--no-passphrase")
    assert r.returncode == 0, r.stderr
    csv = tmp_path / "d.csv"
    csv.write_text("a,b\n1,x\n2,y\n3,x\n")
    out = tmp_path / "d.shape"
    r = _cli("profile", str(csv), "-o", str(out), "--sign", f"{prefix}.key")
    assert r.returncode == 0, r.stderr
    assert "signed_by" in r.stdout
    assert _cli("verify", str(out), "--key", f"{prefix}.pub").returncode == 0
    assert _cli("inspect", str(out), "--verify", f"{prefix}.pub").returncode == 0

    # a second, unsigned artifact fails --verify with exit 1 and a clear message
    plain = tmp_path / "plain.shape"
    assert _cli("profile", str(csv), "-o", str(plain)).returncode == 0
    r = _cli("inspect", str(plain), "--verify", f"{prefix}.pub")
    assert r.returncode == 1 and "signature check failed" in r.stderr
    assert _cli("verify", str(plain), "--key", f"{prefix}.pub").returncode == 1

    # sign it in place, then forge it again
    assert _cli("sign", str(plain), "--key", f"{prefix}.key").returncode == 0
    assert _cli("verify", str(plain), "--key", f"{prefix}.pub").returncode == 0
    members = _members(plain)
    manifest = json.loads(members["manifest.json"])
    manifest["name"] = "forged"
    members["manifest.json"] = canonical_json(manifest)
    _rewrite(plain, members)
    r = _cli("verify", str(plain), "--key", f"{prefix}.pub")
    assert r.returncode == 1
