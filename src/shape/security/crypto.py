"""Authenticated encryption and signing reference provider."""

import os
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from shape.errors import ShapeSecurityError


@dataclass(frozen=True, slots=True)
class EncryptedPayload:
    nonce: bytes
    ciphertext: bytes
    algorithm: str = "AES-256-GCM"


def encrypt_aes_gcm(plaintext, key, aad=b""):
    if len(key) != 32:
        raise ValueError("AES-256-GCM key must be 32 bytes")
    n = os.urandom(12)
    return EncryptedPayload(n, AESGCM(key).encrypt(n, plaintext, aad))


def decrypt_aes_gcm(payload, key, aad=b""):
    if len(key) != 32:
        raise ValueError("AES-256-GCM key must be 32 bytes")
    try:
        return AESGCM(key).decrypt(payload.nonce, payload.ciphertext, aad)
    except Exception as e:
        raise ShapeSecurityError("ciphertext authentication failed") from e


def generate_ed25519_keypair():
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


def sign_ed25519(message, private_key):
    return Ed25519PrivateKey.from_private_bytes(private_key).sign(message)


def verify_ed25519(message, signature, public_key):
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)
    except Exception as e:
        raise ShapeSecurityError("signature verification failed") from e
