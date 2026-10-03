"""Vault errors. No message carries a key, a decrypted value or a secret; column names, policies
and key ids (the first 16 hex of a SHA-256) are the only content a message may name."""

from __future__ import annotations

from shape import compat
from shape.errors import ShapeError


class VaultError(ShapeError):
    """Base class of the vault's failures."""


class VaultFormatError(VaultError, ValueError):
    """The file is not a well-formed vault, or is a newer version than this release reads
    (exit code 2)."""


class VaultInputError(VaultError, ValueError):
    """Bad input: a key that does not decode to 32 bytes, an unreadable key reference, a policy
    that does not fit the profile (exit code 2)."""


class VaultMismatchError(VaultError):
    """A check failed: the wrong key, a vault that is not the profile's, an authentication
    failure (exit code 1)."""


class KekMismatchError(VaultMismatchError):
    """The key-encryption key is not the one the vault was sealed with."""


class VaultAuthenticationError(VaultMismatchError):
    """A part of the vault failed AES-GCM authentication: it was altered, truncated, moved from
    another column, vault or profile, or sealed under another key."""


class VaultReferenceError(VaultMismatchError):
    """The vault is not the one the profile refers to (hash, id or profile id)."""


#: A vault written by a newer release; carries ``found``, ``supported`` and ``min_shape_version``.
VaultVersionError = compat.error_class(VaultFormatError)
