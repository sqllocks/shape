"""The ``shape-vault`` file: envelope encryption of the values a safe capture withheld.

A vault is a JSON file::

    {"format": "shape-vault", "version": 1, "shape_version": ..., "min_shape_version": ...,
     "vault_id": <128 random bits, hex>, "profile_content_id": <the profile's shape_content_id>,
     "algorithm": "AES-256-GCM", "kek_id": <first 16 hex of SHA-256 of the KEK>,
     "wrapped_key": {"nonce": b64, "ciphertext": b64},
     "columns": {"TABLE.COLUMN": {"policy": ..., "nonce": b64, "ciphertext": b64}}}

Envelope encryption with nothing custom: a fresh 256-bit data key (``os.urandom``) per write;
each column's payload (canonical JSON, ``shape.artifact.canonical``) is encrypted with AES-256-GCM
under the data key and a fresh 96-bit random nonce; the data key is encrypted with AES-256-GCM
under the key-encryption key. The associated data of every call binds the whole header, so a part
that moves to another column, vault or profile fails authentication::

    prefix  = b"shape-vault-v1\\x00"
    header  = canonical_json({format, version, vault_id, profile_content_id, kek_id,
                              columns: sorted column names})
    aad(wrapped key) = prefix + b"key\\x00" + header
    aad(column)      = prefix + b"column\\x00" + header + b"\\x00"
                       + canonical_json({"name": NAME, "policy": POLICY})

``docs/VAULT.md`` documents the format; the interoperability test decrypts a vault with
``AESGCM`` and these fields alone. Floats cannot enter canonical JSON, so payload values go through
:func:`encode_value` (a float is ``{"float": "<repr>"}``).
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from shape import compat
from shape.artifact.canonical import canonical_json
from shape.errors import ShapeSecurityError
from shape.security.crypto import EncryptedPayload, decrypt_aes_gcm, encrypt_aes_gcm

from .errors import (
    KekMismatchError,
    VaultAuthenticationError,
    VaultFormatError,
    VaultInputError,
    VaultVersionError,
)
from .kek import KEK_BYTES, kek_id

FORMAT = "shape-vault"
VERSION = 1
ALGORITHM = "AES-256-GCM"
POLICIES = ("categories", "extremes", "all", "none")
_PREFIX = b"shape-vault-v1\x00"
_NONCE_BYTES = 12
_TAG_BYTES = 16
MAX_VAULT_BYTES = 256 * 1024 * 1024
MAX_COLUMNS = 100_000
_HEX = re.compile(r"[0-9a-f]+")
_COLUMN_NAME = re.compile(r"[^\x00-\x1f\x7f]{1,512}")


@dataclass(frozen=True)
class VaultColumn:
    policy: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class OpenedVault:
    vault_id: str
    profile_content_id: str
    kek_id: str
    columns: dict[str, VaultColumn]


# --- values --------------------------------------------------------------------------------------


def encode_value(value: Any) -> Any:
    """``value`` as canonical-JSON-safe data: floats and decimals become tagged strings."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return {"float": repr(value)}
    if isinstance(value, Decimal):
        return {"decimal": str(value)}
    return {"text": str(value)}


def decode_value(value: Any) -> Any:
    """Inverse of :func:`encode_value` (a ``text`` value comes back as its string)."""
    if isinstance(value, dict) and len(value) == 1:
        ((tag, body),) = value.items()
        if tag == "float" and isinstance(body, str):
            return float(body)
        if tag == "decimal" and isinstance(body, str):
            return Decimal(body)
        if tag == "text" and isinstance(body, str):
            return body
    return value


# --- primitives ----------------------------------------------------------------------------------


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: Any, what: str, *, minimum: int = 0) -> bytes:
    if not isinstance(text, str):
        raise VaultFormatError(f"malformed vault: {what} is not text")
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise VaultFormatError(f"malformed vault: {what} is not base64") from None
    if len(raw) < minimum:
        raise VaultFormatError(f"malformed vault: {what} is too short")
    return raw


def _header_bytes(
    version: int, vault_id: str, profile_content_id: str, key_id: str, names: list[str]
) -> bytes:
    return canonical_json(
        {
            "format": FORMAT,
            "version": version,
            "vault_id": vault_id,
            "profile_content_id": profile_content_id,
            "kek_id": key_id,
            "columns": sorted(names),
        }
    )


def _aad_key(header: bytes) -> bytes:
    return _PREFIX + b"key\x00" + header


def _aad_column(header: bytes, name: str, policy: str) -> bytes:
    return (
        _PREFIX
        + b"column\x00"
        + header
        + b"\x00"
        + canonical_json({"name": name, "policy": policy})
    )


def _check_kek(kek: bytes) -> None:
    if not isinstance(kek, (bytes, bytearray)) or len(kek) != KEK_BYTES:
        raise VaultInputError(f"the key-encryption key must be {KEK_BYTES} bytes")


# --- sealing -------------------------------------------------------------------------------------


def seal_vault(
    columns: dict[str, tuple[str, dict[str, Any]]], profile_content_id: str, kek: bytes
) -> bytes:
    """The bytes of a new vault: ``columns`` maps ``TABLE.COLUMN`` to ``(policy, payload)``.

    A fresh data key, vault id and nonces on every call: two seals of the same input share no
    secret part."""
    _check_kek(kek)
    if not isinstance(profile_content_id, str) or not _HEX.fullmatch(profile_content_id):
        raise VaultInputError("profile_content_id must be a lowercase hex digest")
    for name, (policy, payload) in columns.items():
        if not _COLUMN_NAME.fullmatch(name):
            raise VaultInputError("a column name is empty, too long or has control characters")
        if policy not in POLICIES:
            raise VaultInputError(f"{name}: unknown policy {policy!r}")
        if not isinstance(payload, dict):
            raise VaultInputError(f"{name}: the payload must be an object")
    vault_id = os.urandom(16).hex()
    key_id = kek_id(bytes(kek))
    header = _header_bytes(VERSION, vault_id, profile_content_id, key_id, list(columns))
    data_key = os.urandom(KEK_BYTES)
    wrapped = encrypt_aes_gcm(data_key, bytes(kek), _aad_key(header))
    sealed_columns: dict[str, Any] = {}
    for name in sorted(columns):
        policy, payload = columns[name]
        enc = encrypt_aes_gcm(canonical_json(payload), data_key, _aad_column(header, name, policy))
        sealed_columns[name] = {
            "policy": policy,
            "nonce": _b64(enc.nonce),
            "ciphertext": _b64(enc.ciphertext),
        }
    doc = compat.stamp(
        "vault",
        {
            "vault_id": vault_id,
            "profile_content_id": profile_content_id,
            "algorithm": ALGORITHM,
            "kek_id": key_id,
            "wrapped_key": {"nonce": _b64(wrapped.nonce), "ciphertext": _b64(wrapped.ciphertext)},
            "columns": sealed_columns,
        },
        aliases=False,
    )
    return _dump(doc)


def _dump(doc: dict[str, Any]) -> bytes:
    return (json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


# --- reading -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedVault:
    version: int
    vault_id: str
    profile_content_id: str
    kek_id: str
    wrapped: EncryptedPayload
    columns: dict[str, tuple[str, EncryptedPayload]]
    doc: dict[str, Any]


def parse_vault(raw: bytes | str) -> ParsedVault:
    """Check the structure of a vault and return its parts. Anything malformed is a
    :class:`VaultFormatError`; a newer ``version`` is a :class:`VaultVersionError` naming the
    minimum Shape release. No key is needed and nothing is decrypted."""
    data = raw.encode("utf-8") if isinstance(raw, str) else raw
    if len(data) > MAX_VAULT_BYTES:
        raise VaultFormatError("malformed vault: file too large")
    try:
        doc = json.loads(data)
    except (ValueError, RecursionError):
        raise VaultFormatError("malformed vault: not JSON") from None
    if not isinstance(doc, dict):
        raise VaultFormatError("malformed vault: not a JSON object")
    try:
        compat.check_format("vault", doc, error=VaultFormatError)
        if compat.FORMAT_KEY not in doc:
            raise VaultFormatError("malformed vault: no format declared")
        version = compat.check_readable("vault", doc, error=VaultFormatError)
    except compat.UnsupportedVersionError as e:
        raise VaultVersionError(
            str(e),
            kind=e.kind,
            found=e.found,
            supported=e.supported,
            min_shape_version=e.min_shape_version,
        ) from None
    except compat.FormatError as e:
        raise VaultFormatError(f"malformed vault: {e}") from None
    vault_id = doc.get("vault_id")
    pid = doc.get("profile_content_id")
    key_id = doc.get("kek_id")
    if not (isinstance(vault_id, str) and len(vault_id) == 32 and _HEX.fullmatch(vault_id)):
        raise VaultFormatError("malformed vault: vault_id is not 128 bits of hex")
    if not (isinstance(pid, str) and _HEX.fullmatch(pid)):
        raise VaultFormatError("malformed vault: profile_content_id is not a hex digest")
    if not (isinstance(key_id, str) and len(key_id) == 16 and _HEX.fullmatch(key_id)):
        raise VaultFormatError("malformed vault: kek_id is not 16 hex characters")
    if doc.get("algorithm") != ALGORITHM:
        raise VaultFormatError(f"malformed vault: the algorithm is not {ALGORITHM}")
    wrapped = doc.get("wrapped_key")
    if not isinstance(wrapped, dict) or set(wrapped) != {"nonce", "ciphertext"}:
        raise VaultFormatError("malformed vault: wrapped_key needs nonce and ciphertext")
    wrapped_p = _payload(wrapped, "wrapped_key")
    cols = doc.get("columns")
    if not isinstance(cols, dict) or len(cols) > MAX_COLUMNS:
        raise VaultFormatError("malformed vault: columns is not an object")
    parsed_cols: dict[str, tuple[str, EncryptedPayload]] = {}
    for name, entry in cols.items():
        if not _COLUMN_NAME.fullmatch(name):
            raise VaultFormatError("malformed vault: a column name is not valid")
        if not isinstance(entry, dict) or set(entry) != {"policy", "nonce", "ciphertext"}:
            raise VaultFormatError(
                f"malformed vault: column {name!r} needs policy, nonce, ciphertext"
            )
        policy = entry["policy"]
        if policy not in POLICIES:
            raise VaultFormatError(f"malformed vault: column {name!r} has an unknown policy")
        parsed_cols[name] = (policy, _payload(entry, f"column {name!r}"))
    return ParsedVault(version, vault_id, pid, key_id, wrapped_p, parsed_cols, doc)


def _payload(entry: dict[str, Any], what: str) -> EncryptedPayload:
    nonce = _unb64(entry["nonce"], f"{what} nonce")
    if len(nonce) != _NONCE_BYTES:
        raise VaultFormatError(f"malformed vault: {what} nonce is not 96 bits")
    return EncryptedPayload(
        nonce, _unb64(entry["ciphertext"], f"{what} ciphertext", minimum=_TAG_BYTES)
    )


def inspect_vault(raw: bytes | str) -> dict[str, Any]:
    """The header of a vault, with each column's policy and ciphertext size; needs no key."""
    p = parse_vault(raw)
    return {
        "format": FORMAT,
        "version": p.version,
        "shape_version": p.doc.get("shape_version"),
        "min_shape_version": p.doc.get("min_shape_version"),
        "vault_id": p.vault_id,
        "profile_content_id": p.profile_content_id,
        "algorithm": ALGORITHM,
        "kek_id": p.kek_id,
        "columns": [
            {"column": name, "policy": policy, "ciphertext_bytes": len(enc.ciphertext)}
            for name, (policy, enc) in sorted(p.columns.items())
        ],
    }


def open_vault(raw: bytes | str, kek: bytes) -> OpenedVault:
    """Decrypt every column. The wrong key is a :class:`KekMismatchError` naming both key ids; any
    part that fails authentication is a :class:`VaultAuthenticationError`. No message carries a key
    or a value."""
    _check_kek(kek)
    p = parse_vault(raw)
    have = kek_id(bytes(kek))
    if have != p.kek_id:
        raise KekMismatchError(
            f"the key-encryption key does not match the vault: the vault was sealed with key "
            f"{p.kek_id}, this key is {have}"
        )
    header = _header_bytes(p.version, p.vault_id, p.profile_content_id, p.kek_id, list(p.columns))
    try:
        data_key = decrypt_aes_gcm(p.wrapped, bytes(kek), _aad_key(header))
    except ShapeSecurityError:
        raise VaultAuthenticationError(
            "the vault's data key failed authentication: the header or the wrapped key was altered"
        ) from None
    out: dict[str, VaultColumn] = {}
    for name, (policy, enc) in p.columns.items():
        try:
            plain = decrypt_aes_gcm(enc, data_key, _aad_column(header, name, policy))
        except ShapeSecurityError:
            raise VaultAuthenticationError(
                f"column {name} failed authentication: it was altered, moved or truncated"
            ) from None
        try:
            payload = json.loads(plain)
        except (ValueError, RecursionError):
            payload = None
        if not isinstance(payload, dict):
            raise VaultAuthenticationError(f"column {name} does not hold a payload object")
        out[name] = VaultColumn(policy, payload)
    return OpenedVault(p.vault_id, p.profile_content_id, p.kek_id, out)
