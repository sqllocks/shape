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

    def commit(self, name: str, data: str | bytes, metadata: dict[str, Any] | None = None) -> str:
        _check("name", name)
        log = self._path("logs", f"{name}.jsonl")
        self._path("refs", name)
        raw = data.encode() if isinstance(data, str) else data
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
            return p.read_text().strip()
        t = self._path("tags", name, ref)
        if t.is_file():
            return t.read_text().strip()
        if any(e.get("content_id") == ref for e in self.log(name)):
            return ref
        raise RegistryError(f"{name}@{ref} is not recorded in the registry")

    def checkout(self, name: str, ref: str = "latest") -> bytes:
        h = self.resolve(name, ref)
        raw = self._path("objects", h).read_bytes()
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
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def refs(self, name: str) -> dict[str, str]:
        _check("name", name)
        p = self._path("refs", name)
        return {x.name: x.read_text().strip() for x in p.iterdir()} if p.exists() else {}
