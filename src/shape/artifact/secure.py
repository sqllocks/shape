"""Signed/encrypted Shape payload envelope."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass

from shape.artifact.canonical import canonical_json


@dataclass(frozen=True, slots=True)
class SecureEnvelope:
    header: dict
    nonce: bytes
    ciphertext: bytes
    signature: bytes

    def to_bytes(self) -> bytes:
        return canonical_json(
            {
                "header": self.header,
                "nonce": base64.b64encode(self.nonce).decode(),
                "ciphertext": base64.b64encode(self.ciphertext).decode(),
                "signature": base64.b64encode(self.signature).decode(),
            }
        )

    @classmethod
    def from_bytes(cls, data: bytes):
        o = json.loads(data)
        return cls(
            o["header"],
            base64.b64decode(o["nonce"]),
            base64.b64decode(o["ciphertext"]),
            base64.b64decode(o["signature"]),
        )


def seal(
    plaintext: bytes, encryption_key: bytes, signing_private_key: bytes, header: dict
) -> SecureEnvelope:
    from shape.security.crypto import encrypt_aes_gcm, sign_ed25519

    h = canonical_json(header)
    p = encrypt_aes_gcm(plaintext, encryption_key, h)
    signed = h + p.nonce + p.ciphertext
    return SecureEnvelope(
        dict(header), p.nonce, p.ciphertext, sign_ed25519(signed, signing_private_key)
    )


def open_envelope(env: SecureEnvelope, encryption_key: bytes, signing_public_key: bytes) -> bytes:
    from shape.security.crypto import EncryptedPayload, decrypt_aes_gcm, verify_ed25519

    h = canonical_json(env.header)
    verify_ed25519(h + env.nonce + env.ciphertext, env.signature, signing_public_key)
    return decrypt_aes_gcm(EncryptedPayload(env.nonce, env.ciphertext), encryption_key, h)
