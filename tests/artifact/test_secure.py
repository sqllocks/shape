import os

import pytest

from shape.artifact.canonical import canonical_json
from shape.artifact.secure import SecureEnvelope, open_envelope, seal
from shape.errors import ShapeSecurityError
from shape.security.crypto import generate_ed25519_keypair


def test_canonical_stable():
    assert canonical_json({"b": 2, "a": 1}) == b'{"a":1,"b":2}'
    with pytest.raises(TypeError):
        canonical_json({"x": 1.2})


def test_secure_envelope():
    ek = os.urandom(32)
    sk, pk = generate_ed25519_keypair()
    e = seal(b"shape", ek, sk, {"format": "shape", "version": 1})
    decoded = SecureEnvelope.from_bytes(e.to_bytes())
    assert open_envelope(decoded, ek, pk) == b"shape"
    bad = SecureEnvelope(
        decoded.header, decoded.nonce, decoded.ciphertext + b"x", decoded.signature
    )
    with pytest.raises(ShapeSecurityError):
        open_envelope(bad, ek, pk)
