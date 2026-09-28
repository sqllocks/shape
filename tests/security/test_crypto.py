import os

import pytest

from shape.errors import ShapeSecurityError
from shape.security.crypto import (
    decrypt_aes_gcm,
    encrypt_aes_gcm,
    generate_ed25519_keypair,
    sign_ed25519,
    verify_ed25519,
)


def test_crypto():
    k = os.urandom(32)
    p = encrypt_aes_gcm(b"secret", k, b"shape")
    assert decrypt_aes_gcm(p, k, b"shape") == b"secret"
    with pytest.raises(ShapeSecurityError):
        decrypt_aes_gcm(p, os.urandom(32), b"shape")
    sk, pk = generate_ed25519_keypair()
    sig = sign_ed25519(b"x", sk)
    verify_ed25519(b"x", sig, pk)
    with pytest.raises(ShapeSecurityError):
        verify_ed25519(b"y", sig, pk)
