"""Authenticated encryption and signing reference provider."""

import os
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from shape.errors import ShapeSecurityError


@dataclass(frozen=True, slots=True)
class EncryptedPayload:
    """Nonce, ciphertext and algorithm of an authenticated encrypted payload."""

    nonce: bytes
    ciphertext: bytes
    algorithm: str = "AES-256-GCM"


def encrypt_aes_gcm(plaintext: bytes, key: bytes, aad: bytes = b"") -> EncryptedPayload:
    """Encrypt bytes with a 32-byte AES key, random nonce and optional authenticated data."""
    if len(key) != 32:
        raise ValueError("AES-256-GCM key must be 32 bytes")
    n = os.urandom(12)
    return EncryptedPayload(n, AESGCM(key).encrypt(n, plaintext, aad))


def decrypt_aes_gcm(payload: EncryptedPayload, key: bytes, aad: bytes = b"") -> bytes:
    """Authenticate and decrypt a payload; reject a wrong key or altered ciphertext."""
    if len(key) != 32:
        raise ValueError("AES-256-GCM key must be 32 bytes")
    try:
        return AESGCM(key).decrypt(payload.nonce, payload.ciphertext, aad)
    except Exception as e:
        raise ShapeSecurityError("ciphertext authentication failed") from e


def generate_ed25519_keypair() -> tuple[bytes, bytes]:
    """Return a new Ed25519 private/public key pair in raw byte form."""
    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key()
    return (
        sk.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        ),
        pk.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw),
    )


def sign_ed25519(message: bytes, private_key: bytes) -> bytes:
    """Return the Ed25519 signature of a message using the raw private key."""
    return Ed25519PrivateKey.from_private_bytes(private_key).sign(message)


def verify_ed25519(message: bytes, signature: bytes, public_key: bytes) -> None:
    """Verify a message signature or raise ShapeSecurityError."""
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)
    except Exception as e:
        raise ShapeSecurityError("signature verification failed") from e


def public_key_from_private(private_key: bytes) -> bytes:
    return (
        Ed25519PrivateKey.from_private_bytes(private_key)
        .public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    )


def encrypt_private_key_pem(private_key: bytes, passphrase: bytes) -> bytes:
    """The key as an encrypted PKCS#8 PEM (``ENCRYPTED PRIVATE KEY``): PBES2 with a key derived
    from ``passphrase`` and a random salt, as OpenSSL reads it."""
    if not passphrase:
        raise ValueError("passphrase must not be empty")
    return Ed25519PrivateKey.from_private_bytes(private_key).private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(passphrase),
    )


def load_private_key_pem(data: bytes, passphrase: bytes | None) -> bytes:
    """The raw 32 bytes of an Ed25519 key in PKCS#8 PEM, encrypted (``passphrase`` required) or
    not. Raises ``ValueError`` without any key or passphrase text when it cannot be read."""
    try:
        key = serialization.load_pem_private_key(data, password=passphrase or None)
    except Exception:  # the library's error types vary; none of their text is passed on
        raise ValueError(
            "cannot decrypt the private key: wrong passphrase or damaged key"
        ) from None
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("the private key is not an Ed25519 key")
    return key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
