from __future__ import annotations

import hashlib
import json
import struct
import zipfile
import zlib
from pathlib import PurePosixPath

from shape.errors import ShapeError


class ArtifactError(ShapeError, ValueError):
    """A .shape artifact cannot be read or written. Also a ``ValueError``, as the reader's
    earlier failures were."""


class ArtifactFormatError(ArtifactError, zipfile.BadZipFile):
    """The file is not a readable zip archive (also a ``zipfile.BadZipFile``)."""


def canonical_json(obj) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _safe(n):
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


def write_artifact(path, manifest: dict, components: dict[str, bytes]):
    if len(components) > 10000:
        raise ArtifactError("too many components")
    for k in components:
        if not _safe(k) or k == "manifest.json":
            raise ArtifactError("unsafe/reserved component path")
    hashes = {k: sha256(v) for k, v in components.items()}
    m = dict(manifest)
    m["content_hashes"] = hashes
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        z.writestr("manifest.json", canonical_json(m))
        for k, v in sorted(components.items()):
            z.writestr(k, v)
    return m


def read_artifact(path, *args, **kwargs):
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
    path,
    max_member_bytes=512 * 1024 * 1024,
    max_total_bytes=1024 * 1024 * 1024,
    max_ratio=200,
    max_members=10000,
):
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
        expected = {"manifest.json", *hashes.keys()}
        unexpected = set(names) - expected
        if unexpected:
            raise ArtifactError(f"unexpected archive members: {sorted(unexpected)[:3]}")
        total = 0
        out = {}
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


def re_full_sha(x):
    return len(x) == 64 and all(c in "0123456789abcdef" for c in x.lower())
