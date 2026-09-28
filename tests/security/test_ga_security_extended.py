import os
import zipfile

import pytest
from cryptography.exceptions import InvalidTag

from shape.artifact import write_shape
from shape.artifact.io import ArtifactError, read_artifact
from shape.artifact.secure import SecureEnvelope, open_envelope, seal
from shape.errors import ShapeSecurityError
from shape.privacy import release_for
from shape.security import SecurityError, scan_secrets, validate_structure
from shape.security.crypto import generate_ed25519_keypair


def _zip(path, members):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in members:
            z.writestr(n, b)


@pytest.mark.parametrize(
    "value",
    [
        "Bearer " + "A" * 50,
        "password=supersecret123",
        "api_key:" + "x" * 30,
        "AKIAABCDEFGHIJKLMNOP",
        "ghp_" + "x" * 40,
        "-----BEGIN PRIVATE KEY-----",
    ],
)
def test_expanded_secret_detection(value):
    assert scan_secrets({"v": value})


@pytest.mark.parametrize("x", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_rejected(x):
    with pytest.raises(SecurityError):
        validate_structure({"x": x})


@pytest.mark.parametrize(
    "name", ["..\\evil", "C:evil", "a\\..\\evil", "/abs", "a/../evil", "a\x00b"]
)
def test_cross_platform_archive_paths_rejected(tmp_path, name):
    p = tmp_path / "x.zip"
    _zip(p, [("manifest.json", b'{"content_hashes":{}}'), (name, b"x")])
    with pytest.raises(ArtifactError):
        read_artifact(p)


def test_manifest_ratio_bomb(tmp_path):
    p = tmp_path / "x.zip"
    _zip(p, [("manifest.json", b" " * 2_000_000)])
    with pytest.raises(ArtifactError):
        read_artifact(p, max_ratio=10)


def test_invalid_hash_entry(tmp_path):
    p = tmp_path / "x.zip"
    _zip(p, [("manifest.json", b'{"content_hashes":{"shape.json":"no"}}'), ("shape.json", b"{}")])
    with pytest.raises(ArtifactError):
        read_artifact(p)


def test_unknown_artifact_classification_rejected(tmp_path):
    with pytest.raises(ValueError):
        write_shape(tmp_path / "x.shape", {"rows": 1}, classification="ULTRA_SECRET")


def test_metadata_secret_rejected(tmp_path):
    with pytest.raises(SecurityError):
        write_shape(tmp_path / "x.shape", {"rows": 1}, metadata={"token": "Bearer " + "A" * 50})


def test_release_downgrade_strips_tight_bounds_and_values():
    s = {
        "rows": 100,
        "columns": {
            "salary": {
                "kind": "numeric",
                "count": 100,
                "min": 1,
                "max": 999999,
                "quantiles": [1, 2, 3],
                "topk": [[999999, 1]],
                "samples": [123],
            }
        },
    }
    r = release_for(s, {"salary": "TOP_SECRET"}, "PUBLIC", source_classification="TOP_SECRET")
    x = r.shape["columns"]["salary"]
    assert all(k not in x for k in ("min", "max", "quantiles", "topk", "samples"))
    assert x["value_evidence_redacted"] and r.shape["release_policy"]["sanitized_derivative"]


def test_secure_envelope_tamper_header_ciphertext_signature():
    key = os.urandom(32)
    sk, pk = generate_ed25519_keypair()
    e = seal(b"secret", key, sk, {"classification": "TOP_SECRET"})
    assert open_envelope(e, key, pk) == b"secret"
    for bad in [
        SecureEnvelope(
            {**e.header, "classification": "PUBLIC"}, e.nonce, e.ciphertext, e.signature
        ),
        SecureEnvelope(
            e.header, e.nonce, e.ciphertext[:-1] + bytes([e.ciphertext[-1] ^ 1]), e.signature
        ),
        SecureEnvelope(
            e.header, e.nonce, e.ciphertext, e.signature[:-1] + bytes([e.signature[-1] ^ 1])
        ),
    ]:
        with pytest.raises((ShapeSecurityError, InvalidTag)):
            open_envelope(bad, key, pk)


def test_wrong_encryption_key_rejected():
    key = os.urandom(32)
    sk, pk = generate_ed25519_keypair()
    e = seal(b"secret", key, sk, {})
    with pytest.raises((ShapeSecurityError, InvalidTag)):
        open_envelope(e, os.urandom(32), pk)
