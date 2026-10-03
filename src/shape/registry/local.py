"""Local-first immutable Shape registry."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from shape.errors import ShapeError

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class RegistryError(ShapeError):
    """A registry name, ref or path is invalid, or a ref is not recorded for that name."""


class RawProfileError(RegistryError):
    """A raw profile (real values) was offered to a registry without ``allow_raw``."""


def is_raw_profile(data: bytes) -> bool:
    """True for a raw profile: a ``.shape`` profile artifact or ``shape profile export`` JSON.

    Both hold up to 500 real values per column. The safe form (``shape profile safe``) is not
    raw, and neither is anything else. JSON is recognised in every encoding ``json.loads`` reads
    (UTF-8 with or without a byte-order mark, UTF-16, UTF-32), and the manifest of a zip is read
    within the artifact reader's size limit (#283). Bytes that are neither give ``False``, never
    an exception (#396)."""
    import io
    import zipfile

    if zipfile.is_zipfile(io.BytesIO(data)):
        from shape.artifact.io import read_manifest_bytes

        try:
            manifest = json.loads(read_manifest_bytes(io.BytesIO(data)))
        except (
            OSError,
            ValueError,
            KeyError,
            EOFError,
            RuntimeError,
            NotImplementedError,
            zipfile.BadZipFile,
        ):
            manifest = None  # RecursionError is a RuntimeError
        if isinstance(manifest, dict) and manifest.get("kind") == "profile":
            return True
    if not _starts_like_an_object(data):
        return False
    try:
        doc = json.loads(data)
    except (ValueError, RecursionError):
        return False
    return isinstance(doc, dict) and doc.get("format") == "shape-profile"


def _starts_like_an_object(data: bytes) -> bool:
    """Whether the first non-blank character of ``data``, in the encoding ``json.loads`` would
    detect, is ``{`` (so a large file of another kind is never parsed whole)."""
    from json import detect_encoding

    head = data[:256].decode(detect_encoding(data[:4]), errors="ignore")
    return head.lstrip("\ufeff \t\r\n")[:1] == "{"


_CONTENT_ID = re.compile(r"^[0-9a-f]{64}$")


def _content_id(path: Path) -> str:
    """The content id a ref or tag file holds; anything else is a corrupt ref."""
    h = path.read_text().strip()
    if not _CONTENT_ID.fullmatch(h):
        raise RegistryError(f"{path} is corrupt: it does not hold a content id")
    return h


def _check(kind: str, value: object) -> str:
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise RegistryError(f"invalid registry {kind}: {value!r}")
    return value


class LocalRegistry:
    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        for x in ("objects", "refs", "tags", "logs"):
            (self.root / x).mkdir(parents=True, exist_ok=True)
        self._real_root = self.root.resolve()

    def _path(self, *parts: str) -> Path:
        """A path under the root; anything that resolves outside it raises RegistryError."""
        p = self.root.joinpath(*parts)
        if not p.resolve().is_relative_to(self._real_root):
            raise RegistryError(f"path escapes the registry root: {'/'.join(parts)}")
        return p

    def commit(
        self,
        name: str,
        data: str | bytes,
        metadata: dict[str, Any] | None = None,
        *,
        allow_raw: bool = False,
    ) -> str:
        """Store ``data`` under ``name`` and return its content id (the sha256 of the bytes).

        A raw profile holds real values, so it is refused unless ``allow_raw`` is true: commit the
        safe form (``shape profile safe``) instead."""
        _check("name", name)
        log = self._path("logs", f"{name}.jsonl")
        self._path("refs", name)
        raw = data.encode() if isinstance(data, str) else data
        if not allow_raw and is_raw_profile(raw):
            raise RawProfileError(
                f"{name}: this is a raw profile: it holds real values from the data (up to 500 per "
                "column). Commit the safe form (`shape profile safe`), or pass allow_raw=True"
            )
        h = hashlib.sha256(raw).hexdigest()
        p = self._path("objects", h)
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-")
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(raw)
                os.replace(tmp, p)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise
        e = {"name": name, "content_id": h, "created_at": time.time(), "metadata": metadata or {}}
        with log.open("a") as f:
            f.write(json.dumps(e, sort_keys=True) + "\n")
        self._write_ref(name, "latest", h)
        return h

    def _write_ref(self, name: str, ref: str, h: str) -> None:
        _check("ref", ref)
        p = self._path("refs", name)
        p.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p, prefix=".tmp-")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(h)
            os.replace(tmp, p / ref)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def resolve(self, name: str, ref: str = "latest") -> str:
        """Resolve a ref, tag or content hash that is recorded for ``name`` (SEC4)."""
        _check("name", name)
        _check("ref", ref)
        p = self._path("refs", name, ref)
        if p.is_file():
            return _content_id(p)
        t = self._path("tags", name, ref)
        if t.is_file():
            return _content_id(t)
        if any(e.get("content_id") == ref for e in self.log(name)):
            return ref
        raise RegistryError(f"{name}@{ref} is not recorded in the registry")

    def checkout(self, name: str, ref: str = "latest") -> bytes:
        h = self.resolve(name, ref)
        try:
            raw = self._path("objects", h).read_bytes()
        except FileNotFoundError:
            raise RegistryError(
                f"object {h} of {name}@{ref} is missing from {self.root / 'objects'}: restore it "
                "from a backup, or commit the content again"
            ) from None
        if hashlib.sha256(raw).hexdigest() != h:
            raise RegistryError(f"object {h} is corrupt: its content does not match its id")
        return raw

    def tag(self, name: str, tag: str, ref: str = "latest") -> str:
        h = self.resolve(name, ref)
        _check("tag", tag)
        p = self._path("tags", name)
        p.mkdir(parents=True, exist_ok=True)
        self._path("tags", name, tag)
        (p / tag).write_text(h)
        return h

    def promote(self, name: str, source: str, target: str) -> str:
        h = self.resolve(name, source)
        self._write_ref(name, target, h)
        return h

    def log(self, name: str) -> list[dict[str, Any]]:
        _check("name", name)
        p = self._path("logs", f"{name}.jsonl")
        if not p.exists():
            return []
        out: list[dict[str, Any]] = []
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                entry = None
            if not isinstance(entry, dict):
                raise RegistryError(
                    f"{p}: line {i} is not a log entry (an interrupted write?); "
                    "remove or repair that line"
                )
            out.append(entry)
        return out

    def refs(self, name: str) -> dict[str, str]:
        _check("name", name)
        p = self._path("refs", name)
        if not p.exists():
            return {}
        # a .tmp- file is a write that was interrupted, never a ref (refs start alphanumeric)
        return {x.name: x.read_text().strip() for x in p.iterdir() if _NAME.fullmatch(x.name)}

    def names(self) -> list[str]:
        """The names with at least one commit, sorted."""
        return sorted(p.name[: -len(".jsonl")] for p in self._path("logs").glob("*.jsonl"))

    def tags(self, name: str) -> dict[str, str]:
        """The tags of ``name`` and the content id each points at."""
        _check("name", name)
        p = self._path("tags", name)
        if not p.exists():
            return {}
        return {
            x.name: x.read_text().strip() for x in sorted(p.iterdir()) if _NAME.fullmatch(x.name)
        }

    def entry(self, name: str, ref: str = "latest") -> dict[str, Any]:
        """The newest log entry whose content is ``ref`` (a ref, tag or content id) of ``name``."""
        cid = self.resolve(name, ref)
        entries: list[dict[str, Any]] = self.log(name)
        for e in reversed(entries):
            if e.get("content_id") == cid:
                return dict(e)
        raise RegistryError(f"{name}@{ref} is not recorded in the registry")
