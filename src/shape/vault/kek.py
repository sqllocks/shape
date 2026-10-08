"""The key-encryption key (KEK): 32 random bytes, kept as base64 text.

A KEK is never a command-line value. It is named by a credential reference: ``env://NAME`` (the
variable holds the base64 text), ``file://PATH`` or a plain path (a file that holds it; a file that
group or others can read is refused), or any other registered scheme such as ``kv://``. Nothing
here puts the key, or the text it was read from, in a message.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import os
import stat
from collections.abc import Mapping
from pathlib import Path

from shape.security import credrefs

from .errors import VaultInputError

KEK_BYTES = 32


def kek_id(kek: bytes) -> str:
    """First 16 hex of the SHA-256 of the key: names a key without revealing it."""
    return hashlib.sha256(kek).hexdigest()[:16]


def generate_kek() -> bytes:
    return os.urandom(KEK_BYTES)


def encode_kek(kek: bytes) -> str:
    return base64.b64encode(kek).decode("ascii")


def write_kek(path: str | os.PathLike[str]) -> str:
    """Write a new KEK (base64 of 32 random bytes) to ``path`` with mode 0600 where the OS has
    mode bits, and return its key id. Never overwrites a file."""
    target = Path(path)
    kek = generate_kek()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(target, flags, stat.S_IRUSR | stat.S_IWUSR)
    except FileExistsError:
        raise VaultInputError(f"{target} exists: keygen never overwrites a key file") from None
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(encode_kek(kek).encode("ascii") + b"\n")
    except BaseException:
        with contextlib.suppress(OSError):
            target.unlink()
        raise
    if os.name == "posix":
        os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
    return kek_id(kek)


def decode_kek(text: str) -> bytes:
    """The 32 key bytes of base64 ``text``; anything else is a :class:`VaultInputError` that does
    not repeat the text."""
    try:
        raw = base64.b64decode(text.strip(), validate=True)
    except (binascii.Error, ValueError):
        raw = b""
    if len(raw) != KEK_BYTES:
        raise VaultInputError(
            f"the key-encryption key must be base64 of {KEK_BYTES} bytes (make one with "
            "`shape vault keygen -o KEK.key`)"
        )
    return raw


def _is_key_text(text: str) -> bool:
    try:
        return len(base64.b64decode(text.strip(), validate=True)) == KEK_BYTES
    except (binascii.Error, ValueError):
        return False


def resolve_kek(ref: str, *, environ: Mapping[str, str] | None = None) -> bytes:
    """The KEK behind ``ref``: ``env://NAME``, ``file://PATH``, ``kv://...`` or a plain path."""
    if not isinstance(ref, str) or not ref:
        raise VaultInputError("a key-encryption key reference is required (--kek REF)")
    if not credrefs.is_reference(ref):
        if _is_key_text(ref) and not os.path.exists(ref):
            # a key typed where a reference belongs: say so without printing it back
            raise VaultInputError(
                "--kek takes a reference (env://NAME, file://PATH or a path), never the key itself"
            )
        if "://" in ref:
            raise VaultInputError("the key reference scheme is not one of env://, file://, kv://")
        ref = "file://" + ref
    try:
        text = credrefs.resolve_reference(ref, environ=environ)
    except credrefs.CredentialReferenceError as e:
        raise VaultInputError(f"cannot read the key-encryption key: {e}") from None
    return decode_kek(text)
