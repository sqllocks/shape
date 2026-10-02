from __future__ import annotations

import hashlib
import json
import struct
import zipfile
import zlib
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


def read_artifact(path: Any, *args: Any, **kwargs: Any) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Read a .shape archive: ``(manifest, {component: bytes})``. Every failure of a bad file is
    an ``ArtifactError`` (P18); a missing or unreadable path keeps its ``OSError``."""
    try:
        return _read_artifact(path, *args, **kwargs)
    except ArtifactError:
        raise
    except zipfile.BadZipFile as e:
        raise ArtifactFormatError(f"not a readable .shape archive: {e}") from e
    except (zlib.error, EOFError, NotImplementedError, RuntimeError, KeyError, struct.error) as e:
        raise ArtifactError(f"corrupt .shape archive: {type(e).__name__}: {e}") from e


def _read_artifact(
    path: Any,
    max_member_bytes: int = 512 * 1024 * 1024,
    max_total_bytes: int = 1024 * 1024 * 1024,
    max_ratio: int = 200,
    max_members: int = 10000,
    verify_key: bytes | None = None,
) -> tuple[dict[str, Any], dict[str, bytes]]:
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
        if mi.file_size > 4 * 1024 * 1024:
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

            sig = z.read(SIGNATURE_MEMBER) if SIGNATURE_MEMBER in names else None
            verify_manifest_signature(rawm, sig, verify_key)
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
        return m, out


def re_full_sha(x: str) -> bool:
    return len(x) == 64 and all(c in "0123456789abcdef" for c in x.lower())
