"""Ed25519 signing and verification of ``.shape`` artifacts (P7-03, fixes P19).

An artifact's own hashes only detect accidents: anyone can edit a component and rewrite
``content_hashes`` and ``shape_content_id`` to match. The signature closes that. It covers the
exact bytes of ``manifest.json``, which carry every content hash, and sits in the reserved
archive member ``manifest.sig`` outside the hashed set. Changing a component means changing the
manifest, which invalidates the signature; stripping the signature makes verification fail;
signing with another key fails against the trusted public key.

Needs the ``[sign]`` extra (``cryptography``). Key handling is described in
``docs/SIGNING.md``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from shape import compat

from .io import ArtifactSignatureError, read_artifact, write_container
from .keys import Passphrase, PassphraseSource
from .keys import load_private_key as _load_private_key
from .keys import load_public_key as _load_public_key
from .keys import write_keypair as _write_keypair

ALGORITHM = "Ed25519"
# Domain separation: a signature over a manifest is never valid for any other message.
DOMAIN = b"shape-artifact-signature-v1\n"
_KEY_BYTES = 32


def _require_crypto() -> None:
    try:
        import cryptography  # noqa: F401
    except ImportError as e:
        raise ImportError(
            "artifact signing needs the 'cryptography' package: pip install 'sqllocks-shape[sign]'"
        ) from e


def key_id(public_key: bytes) -> str:
    """Short fingerprint of a public key (first 16 hex of its SHA-256)."""
    return hashlib.sha256(public_key).hexdigest()[:16]


def generate_keypair() -> tuple[bytes, bytes]:
    """``(private_key, public_key)``, 32 raw bytes each."""
    _require_crypto()
    from shape.security.crypto import generate_ed25519_keypair

    sk, pk = generate_ed25519_keypair()
    return bytes(sk), bytes(pk)


def write_keypair(
    prefix: str | os.PathLike[str],
    passphrase: Passphrase = None,
    *,
    unencrypted: bool = False,
) -> tuple[Path, Path]:
    """Generate a key pair and write ``<prefix>.key`` (private, mode 0600 where the OS has mode
    bits) and ``<prefix>.pub``.

    The private key is encrypted with ``passphrase`` (PKCS#8 PEM). Without one, ``unencrypted=True``
    must ask for the raw form explicitly, and a warning follows. Never overwrites a file."""
    _require_crypto()
    return _write_keypair(prefix, passphrase, unencrypted=unencrypted, generate=generate_keypair)


def load_private_key(source: str | os.PathLike[str], passphrase: PassphraseSource = None) -> bytes:
    """The private key behind ``source``: a file path, ``-`` (standard input), ``env://NAME``,
    ``file://PATH`` or ``kv://...``. An encrypted key needs ``passphrase`` (a value, or a callable
    asked only when the key is encrypted)."""
    return _load_private_key(source, passphrase)


def load_public_key(source: str | os.PathLike[str]) -> bytes:
    """A public key from a file written by :func:`write_keypair`, or any other key source."""
    return _load_public_key(source)


def public_key_of(private_key: bytes) -> bytes:
    _require_crypto()
    from shape.security.crypto import public_key_from_private

    return bytes(public_key_from_private(private_key))


def _message(manifest_bytes: bytes) -> bytes:
    return DOMAIN + manifest_bytes


def verify_manifest_signature(
    manifest_bytes: bytes, signature_member: bytes | None, public_key: bytes
) -> None:
    """Raise :class:`ArtifactSignatureError` unless ``signature_member`` is a valid signature
    of ``manifest_bytes`` under ``public_key``."""
    if len(public_key) != _KEY_BYTES:
        raise ValueError("public key must be 32 bytes")
    if signature_member is None:
        raise ArtifactSignatureError("artifact is not signed")
    try:
        doc = json.loads(signature_member)
    except (ValueError, RecursionError) as e:
        raise ArtifactSignatureError(f"malformed signature: {type(e).__name__}") from e
    if isinstance(doc, dict):
        # a newer signature format is refused as such, naming the release that reads it
        compat.check_format("signature", doc, error=ArtifactSignatureError)
        compat.check_readable("signature", doc, error=ArtifactSignatureError)
    try:
        if not isinstance(doc, dict) or doc.get("algorithm") != ALGORITHM:
            raise ValueError("unsupported signature algorithm")
        sig = base64.b64decode(str(doc["signature"]), validate=True)
        kid = doc.get("key_id")
    except (ValueError, KeyError, binascii.Error, RecursionError) as e:
        raise ArtifactSignatureError(f"malformed signature: {type(e).__name__}") from e
    if kid != key_id(public_key):
        raise ArtifactSignatureError("artifact is signed by a different key than the trusted key")
    _require_crypto()
    from shape.errors import ShapeSecurityError
    from shape.security.crypto import verify_ed25519

    try:
        verify_ed25519(_message(manifest_bytes), sig, public_key)
    except ShapeSecurityError as e:
        raise ArtifactSignatureError("signature does not match the artifact manifest") from e


def sign_artifact(
    path: str | os.PathLike[str],
    private_key: bytes,
    out: str | os.PathLike[str] | None = None,
) -> str:
    """Sign the artifact at ``path`` (in place, or into ``out``) and return the key id.

    The artifact must read cleanly first, so a damaged file is never signed. Signing again
    replaces the earlier signature."""
    if len(private_key) != _KEY_BYTES:
        raise ValueError("private key must be 32 bytes")
    _require_crypto()
    from shape.security.crypto import sign_ed25519

    public_key = public_key_of(private_key)
    read_artifact(path, notice=False)
    with zipfile.ZipFile(path) as src:
        manifest_bytes = src.read("manifest.json")
    sig = sign_ed25519(_message(manifest_bytes), private_key)
    member = json.dumps(
        compat.stamp(
            "signature",
            {
                "algorithm": ALGORITHM,
                "key_id": key_id(public_key),
                "signature": base64.b64encode(sig).decode(),
            },
            aliases=False,
        ),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    _, components = read_artifact(path, notice=False)
    # in place: the file a symbolic link points at is signed, and the link stays a link
    target = Path(out) if out is not None else Path(os.path.realpath(path))
    fd, tmp_name = tempfile.mkstemp(dir=target.parent or ".", suffix=".tmp")
    os.close(fd)
    try:
        # The signature covers the manifest bytes only; the container is rebuilt in the canonical
        # layout, so a signed file is as reproducible as an unsigned one.
        write_container(tmp_name, manifest_bytes, components, member)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    # Replace only after the source is closed: Windows cannot replace a file that is open.
    try:
        # mkstemp creates the file 0600: give it the target's mode, or a new file's (#405)
        os.chmod(tmp_name, _mode_for(target))
        os.replace(tmp_name, target)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return key_id(public_key)


def _mode_for(target: Path) -> int:
    """The permission bits a signed file gets: the existing file's, or ``0o666`` less the
    umask for a new one (what any other write of the artifact would give)."""
    try:
        return os.stat(target).st_mode & 0o7777
    except FileNotFoundError:
        umask = os.umask(0)
        os.umask(umask)
        return 0o666 & ~umask


def verify_artifact(path: str | os.PathLike[str], public_key: bytes) -> dict[str, Any]:
    """Verify the signature and every content hash of the artifact at ``path``.

    Returns ``{"verified": True, "key_id": ...}``; raises :class:`ArtifactSignatureError` when
    the artifact is unsigned, signed by another key or altered, and ``ArtifactError`` when it
    is otherwise defective."""
    read_artifact(path, verify_key=public_key)
    return {"verified": True, "key_id": key_id(public_key)}
