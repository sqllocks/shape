from __future__ import annotations

import hashlib
import json
import os
import struct
import warnings
import zipfile
import zlib
from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Any

from shape.errors import ShapeError


class ArtifactError(ShapeError, ValueError):
    """A .shape artifact cannot be read or written. Also a ``ValueError``, as the reader's
    earlier failures were."""


class ArtifactFormatError(ArtifactError, zipfile.BadZipFile):
    """The file is not a readable zip archive (also a ``zipfile.BadZipFile``)."""


class ArtifactSignatureError(ArtifactError):
    """The artifact is unsigned, or its signature does not verify under the trusted key (P19)."""


class ArtifactNotVerifiedWarning(UserWarning):
    """An artifact was read without its signature being verified (unsigned, or signed and the
    reader was given no trusted key). Not an error: a read without a key never fails on it."""


SignatureInfo = dict[str, Any]


class ArtifactRead(tuple[dict[str, Any], dict[str, Any]]):
    """``(manifest, components)`` as before, plus ``.signature``: ``{"status", "verified",
    "key_id"}`` with status ``verified`` (checked against the key given), ``unsigned`` or
    ``signed_not_verified`` (a signature is present and no trusted key was given to check it)."""

    signature: SignatureInfo

    def __new__(cls, manifest: Any, components: Any, signature: SignatureInfo) -> ArtifactRead:
        self = super().__new__(cls, (manifest, components))
        self.signature = signature
        return self


def not_verified_message(path: Any, info: SignatureInfo) -> str:
    """The notice for an artifact whose signature was not verified, or ``""`` when it was."""
    label = path if isinstance(path, (str, os.PathLike)) else "artifact"
    label = os.fspath(label) if isinstance(label, os.PathLike) else label
    if info["status"] == "unsigned":
        return f"{label} is not signed: its origin is not verified (check it with --verify PUBKEY)"
    if info["status"] == "signed_not_verified":
        return (
            f"{label} is signed by key {info.get('key_id') or 'unknown'}, but the signature was "
            "not verified: no trusted key was given (check it with --verify PUBKEY)"
        )
    return ""


def _default_notice(message: str) -> None:
    warnings.warn(message, ArtifactNotVerifiedWarning, stacklevel=4)


_notice_handler: Callable[[str], None] = _default_notice


def set_notice_handler(handler: Callable[[str], None] | None) -> Callable[[str], None]:
    """Route "signature not verified" notices to ``handler`` (``None`` restores the default, an
    :class:`ArtifactNotVerifiedWarning`). Returns the previous handler."""
    global _notice_handler
    previous = _notice_handler
    _notice_handler = handler or _default_notice
    return previous


SIGNATURE_MEMBER = "manifest.sig"
_MAX_SIGNATURE_BYTES = 4096


def canonical_json(obj: Any) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _safe(n: object) -> bool:
    if not isinstance(n, str) or not n or "\x00" in n or "\\" in n:
        return False
    p = PurePosixPath(n)
    if p.is_absolute() or ".." in p.parts or "." in p.parts:
        return False
    if any(not x for x in p.parts):
        return False
    first = p.parts[0]
    if ":" in first:
        return False
    return True


# Container reproducibility (SAC-01). The signed manifest bytes never depend on the container, but
# the .shape file itself is committed to git, so identical content must give identical bytes:
# a fixed member timestamp, a fixed member order (manifest, components sorted by name, then the
# signature), a fixed creator system and permissions, and no compression. Deflate output depends
# on the zlib build (zlib, zlib-ng, ...), so only ``ZIP_STORED`` is identical across machines;
# git compresses and delta-packs the bytes itself. Readers accept every method.
_FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)
_FIXED_CREATOR_SYSTEM = 3  # Unix, whatever OS wrote the file
_FIXED_EXTERNAL_ATTR = 0o100644 << 16


def write_zip_member(z: zipfile.ZipFile, name: str, data: bytes) -> None:
    """Add one member with every volatile field fixed."""
    zi = zipfile.ZipInfo(name, _FIXED_DATE_TIME)
    zi.compress_type = zipfile.ZIP_STORED
    zi.create_system = _FIXED_CREATOR_SYSTEM
    zi.external_attr = _FIXED_EXTERNAL_ATTR
    z.writestr(zi, data)


def write_container(
    path: Any, manifest_bytes: bytes, components: dict[str, bytes], signature: bytes | None = None
) -> None:
    """Write the zip container: manifest, components sorted by name, then the optional
    signature. The same inputs always give the same bytes."""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as z:
        write_zip_member(z, "manifest.json", manifest_bytes)
        for k, v in sorted(components.items()):
            write_zip_member(z, k, v)
        if signature is not None:
            write_zip_member(z, SIGNATURE_MEMBER, signature)


def write_artifact(
    path: Any, manifest: dict[str, Any], components: dict[str, bytes]
) -> dict[str, Any]:
    if len(components) > 10000:
        raise ArtifactError("too many components")
    for k in components:
        if not _safe(k) or k in ("manifest.json", SIGNATURE_MEMBER):
            raise ArtifactError("unsafe/reserved component path")
    hashes = {k: sha256(v) for k, v in components.items()}
    m = dict(manifest)
    m["content_hashes"] = hashes
    write_container(path, canonical_json(m), components)
    return m


def read_artifact(path: Any, *args: Any, **kwargs: Any) -> ArtifactRead:
    """Read a .shape archive: ``(manifest, {component: bytes})``, a tuple that also carries
    ``.signature``. Every failure of a bad file is an ``ArtifactError`` (P18); a missing or
    unreadable path keeps its ``OSError``.

    Without ``verify_key`` the signature is not checked, and the read says so: a notice
    (a warning, or the CLI's stderr line) names an unsigned or unverified artifact. ``notice=False``
    silences it for a caller that has its own reason (it never changes what is accepted)."""
    notice = kwargs.pop("notice", True)
    try:
        result = _read_artifact(path, *args, **kwargs)
        if notice:
            message = not_verified_message(path, result.signature)
            if message:
                _notice_handler(message)
        return result
    except ArtifactError:
        raise
    except zipfile.BadZipFile as e:
        raise ArtifactFormatError(f"not a readable .shape archive: {e}") from e
    except (zlib.error, EOFError, NotImplementedError, RuntimeError, KeyError, struct.error) as e:
        raise ArtifactError(f"corrupt .shape archive: {type(e).__name__}: {e}") from e


MAX_MANIFEST_BYTES = 4 * 1024 * 1024


def read_manifest_bytes(path: Any) -> bytes:
    """The raw ``manifest.json`` of a container, read within ``MAX_MANIFEST_BYTES``.

    For the callers that only need the manifest (a kind sniff, a name): a bare
    ``ZipFile.read`` would inflate a hostile member of any size into memory (P7-04)."""
    with zipfile.ZipFile(path) as z:
        info = z.getinfo("manifest.json")
        if info.file_size > MAX_MANIFEST_BYTES:
            raise ArtifactError("manifest too large")
        with z.open(info) as fh:
            raw = fh.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ArtifactError("manifest too large")
    return raw


def _read_artifact(
    path: Any,
    max_member_bytes: int = 512 * 1024 * 1024,
    max_total_bytes: int = 1024 * 1024 * 1024,
    max_ratio: int = 200,
    max_members: int = 10000,
    verify_key: bytes | None = None,
) -> ArtifactRead:
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        names = [i.filename for i in infos]
        if len(infos) > max_members:
            raise ArtifactError("too many archive members")
        if len(names) != len(set(names)):
            raise ArtifactError("duplicate archive member")
        if any(not _safe(n) for n in names):
            raise ArtifactError("unsafe archive path")
        if "manifest.json" not in names:
            raise ArtifactError("missing manifest")
        mi = z.getinfo("manifest.json")
        if mi.file_size > MAX_MANIFEST_BYTES:
            raise ArtifactError("manifest too large")
        if mi.compress_size and mi.file_size / mi.compress_size > max_ratio:
            raise ArtifactError("manifest compression ratio limit")
        try:
            rawm = z.read("manifest.json")
            m = json.loads(rawm)
        except Exception as e:
            raise ArtifactError("invalid manifest") from e
        if verify_key is not None:
            # The signature covers the exact manifest bytes (which carry every content hash),
            # so it is checked before anything in the manifest is trusted.
            from .signing import verify_manifest_signature

            sig = None
            if SIGNATURE_MEMBER in names:
                if z.getinfo(SIGNATURE_MEMBER).file_size > _MAX_SIGNATURE_BYTES:
                    raise ArtifactError("signature too large")
                sig = z.read(SIGNATURE_MEMBER)
            verify_manifest_signature(rawm, sig, verify_key)
            from .signing import key_id

            info: SignatureInfo = {
                "status": "verified",
                "verified": True,
                "key_id": key_id(verify_key),
            }
        else:
            info = _unverified_info(z, names)
        if not isinstance(m, dict):
            raise ArtifactError("manifest must be object")
        hashes = m.get("content_hashes", {})
        if not isinstance(hashes, dict) or len(hashes) > max_members - 1:
            raise ArtifactError("invalid content_hashes")
        if any(
            not isinstance(k, str) or not isinstance(v, str) or not re_full_sha(v)
            for k, v in hashes.items()
        ):
            raise ArtifactError("invalid content hash entry")
        if "manifest.json" in hashes or SIGNATURE_MEMBER in hashes:
            # the writer refuses these names; a component must never alias the manifest or the
            # signature (signing such a file would write the signature member twice, #407)
            raise ArtifactError("unsafe/reserved component path in content_hashes")
        expected = {"manifest.json", SIGNATURE_MEMBER, *hashes.keys()}
        if (
            SIGNATURE_MEMBER in names
            and z.getinfo(SIGNATURE_MEMBER).file_size > _MAX_SIGNATURE_BYTES
        ):
            raise ArtifactError("signature too large")
        unexpected = set(names) - expected
        if unexpected:
            raise ArtifactError(f"unexpected archive members: {sorted(unexpected)[:3]}")
        total = 0
        out: dict[str, bytes] = {}
        for n, h in hashes.items():
            if not _safe(n) or n not in names:
                raise ArtifactError(f"missing/unsafe component {n}")
            i = z.getinfo(n)
            total += i.file_size
            if i.file_size > max_member_bytes or total > max_total_bytes:
                raise ArtifactError("artifact size limit")
            if i.compress_size == 0 and i.file_size > 0:
                raise ArtifactError("invalid compressed member")
            if i.compress_size and i.file_size / i.compress_size > max_ratio:
                raise ArtifactError("compression ratio limit")
            b = z.read(n)
            if len(b) != i.file_size:
                raise ArtifactError("truncated archive member")
            if sha256(b) != h:
                raise ArtifactError(f"checksum mismatch {n}")
            out[n] = b
        return ArtifactRead(m, out, info)


def _unverified_info(z: zipfile.ZipFile, names: list[str]) -> SignatureInfo:
    """What a read without a trusted key can say: unsigned, or signed by key ``key_id``. Nothing
    is checked here, so ``verified`` is false either way."""
    if SIGNATURE_MEMBER not in names:
        return {"status": "unsigned", "verified": False, "key_id": None}
    kid: Any = None
    if z.getinfo(SIGNATURE_MEMBER).file_size <= _MAX_SIGNATURE_BYTES:
        try:
            doc = json.loads(z.read(SIGNATURE_MEMBER))
            kid = doc.get("key_id") if isinstance(doc, dict) else None
        except (ValueError, RecursionError):
            kid = None
    return {
        "status": "signed_not_verified",
        "verified": False,
        # the id is shown in a notice, so only a plain hex fingerprint is passed on
        "key_id": kid if isinstance(kid, str) and re_hex(kid) else None,
    }


def re_hex(x: str) -> bool:
    return 0 < len(x) <= 64 and all(c in "0123456789abcdef" for c in x)


def re_full_sha(x: str) -> bool:
    return len(x) == 64 and all(c in "0123456789abcdef" for c in x.lower())
