"""Regression tests for the AUD-privacy audit (artifact package)."""

from __future__ import annotations

import os
import stat
import warnings

import pytest

from shape.artifact.keys import load_private_key, load_public_key, write_keypair
from shape.artifact.signing import generate_keypair
from shape.security.credrefs import CredentialReferenceError

posix_only = pytest.mark.skipif(os.name != "posix", reason="mode bits are POSIX only")


def _keypair(tmp_path):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return write_keypair(tmp_path / "k", None, unencrypted=True, generate=generate_keypair)


# --- #402: one permission rule for a private key file -------------------------------------------


@posix_only
@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604])
def test_a_plain_path_private_key_others_can_read_is_refused(tmp_path, mode):
    priv, _ = _keypair(tmp_path)
    os.chmod(priv, mode)
    with pytest.raises(CredentialReferenceError, match="chmod 600"):
        load_private_key(str(priv))


@posix_only
def test_a_plain_path_private_key_with_mode_0600_is_read(tmp_path):
    priv, _ = _keypair(tmp_path)
    assert stat.S_IMODE(os.stat(priv).st_mode) == 0o600
    assert len(load_private_key(str(priv))) == 32


@posix_only
def test_a_world_readable_public_key_is_still_read(tmp_path):
    _, pub = _keypair(tmp_path)
    os.chmod(pub, 0o644)
    assert len(load_public_key(str(pub))) == 32


# --- #405: signing keeps the artifact's file mode ------------------------------------------------


@posix_only
def test_signing_in_place_keeps_the_file_mode(tmp_path):
    from shape.artifact import sign_artifact
    from shape.artifact.io import write_artifact

    p = tmp_path / "a.shape"
    write_artifact(p, {"format": "x"}, {"a.json": b"{}"})
    os.chmod(p, 0o644)
    sign_artifact(p, generate_keypair()[0])
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o644


@posix_only
def test_signing_into_a_new_file_gives_the_umask_mode(tmp_path):
    from shape.artifact import sign_artifact
    from shape.artifact.io import write_artifact

    src, out = tmp_path / "a.shape", tmp_path / "b.shape"
    write_artifact(src, {"format": "x"}, {"a.json": b"{}"})
    old = os.umask(0o022)
    try:
        sign_artifact(src, generate_keypair()[0], out=out)
    finally:
        os.umask(old)
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o644


# --- #407: reserved member names are refused on read --------------------------------------------


@pytest.mark.parametrize("reserved", ["manifest.sig", "manifest.json"])
def test_content_hashes_naming_a_reserved_member_is_refused(tmp_path, reserved):
    import hashlib
    import json
    import zipfile

    from shape.artifact.io import ArtifactError, read_artifact

    body = b'{"x":1}'
    p = tmp_path / "r.shape"
    with zipfile.ZipFile(p, "w") as z:
        hashes = {reserved: hashlib.sha256(body).hexdigest()}
        z.writestr("manifest.json", json.dumps({"format": "x", "content_hashes": hashes}))
        if reserved == "manifest.sig":
            z.writestr("manifest.sig", body)
    with pytest.raises(ArtifactError, match="reserved"):
        read_artifact(p, notice=False)


# --- #428: a malformed secure envelope is one typed error ---------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        b"{}",
        b"[]",
        b"x",
        b'{"header":{},"nonce":"!!","ciphertext":"","signature":""}',
        b'{"header":[],"nonce":"","ciphertext":"","signature":""}',
        b'{"header":{},"nonce":5,"ciphertext":"","signature":""}',
        b"\xff",
    ],
    ids=["empty", "list", "not-json", "bad-base64", "header-list", "nonce-number", "not-utf8"],
)
def test_a_malformed_envelope_is_a_security_error(data):
    from shape.artifact.secure import SecureEnvelope
    from shape.errors import ShapeSecurityError

    with pytest.raises(ShapeSecurityError, match="malformed envelope"):
        SecureEnvelope.from_bytes(data)


def test_an_envelope_round_trips():
    from shape.artifact.secure import SecureEnvelope, open_envelope, seal

    sk, pk = generate_keypair()
    key = b"k" * 32
    env = seal(b"secret", key, sk, {"v": 1})
    assert open_envelope(SecureEnvelope.from_bytes(env.to_bytes()), key, pk) == b"secret"
