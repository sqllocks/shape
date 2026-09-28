"""Local-first immutable Shape registry."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path


class LocalRegistry:
    def __init__(self, root):
        self.root = Path(root)
        for x in ("objects", "refs", "tags", "logs"):
            (self.root / x).mkdir(parents=True, exist_ok=True)

    def commit(self, name, data, metadata=None):
        if isinstance(data, str):
            data = data.encode()
        h = hashlib.sha256(data).hexdigest()
        p = self.root / "objects" / h
        if not p.exists():
            p.write_bytes(data)
        e = {"name": name, "content_id": h, "created_at": time.time(), "metadata": metadata or {}}
        with (self.root / "logs" / f"{name}.jsonl").open("a") as f:
            f.write(json.dumps(e, sort_keys=True) + "\n")
        self._write_ref(name, "latest", h)
        return h

    def _write_ref(self, name, ref, h):
        p = self.root / "refs" / name
        p.mkdir(parents=True, exist_ok=True)
        tmp = p / (ref + ".tmp")
        tmp.write_text(h)
        os.replace(tmp, p / ref)

    def resolve(self, name, ref="latest"):
        p = self.root / "refs" / name / ref
        if p.exists():
            return p.read_text().strip()
        t = self.root / "tags" / name / ref
        if t.exists():
            return t.read_text().strip()
        if (self.root / "objects" / ref).exists():
            return ref
        raise KeyError(f"{name}@{ref}")

    def checkout(self, name, ref="latest"):
        return (self.root / "objects" / self.resolve(name, ref)).read_bytes()

    def tag(self, name, tag, ref="latest"):
        h = self.resolve(name, ref)
        p = self.root / "tags" / name
        p.mkdir(parents=True, exist_ok=True)
        (p / tag).write_text(h)
        return h

    def promote(self, name, source, target):
        h = self.resolve(name, source)
        self._write_ref(name, target, h)
        return h

    def log(self, name):
        p = self.root / "logs" / f"{name}.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def refs(self, name):
        p = self.root / "refs" / name
        return {x.name: x.read_text().strip() for x in p.iterdir()} if p.exists() else {}
