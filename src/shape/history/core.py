from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HistoryEntry:
    content_hash: str
    created_at: float
    parent: str | None
    metadata: dict


@dataclass(frozen=True)
class ShapeDelta:
    before: str
    after: str
    changes: tuple[dict, ...]


class LocalHistory:
    def __init__(self, root):
        self.root = Path(root)
        (self.root / "objects").mkdir(parents=True, exist_ok=True)
        self.index = self.root / "index.jsonl"

    def append(self, data: bytes, parent=None, metadata=None):
        h = hashlib.sha256(data).hexdigest()
        p = self.root / "objects" / h
        if not p.exists():
            p.write_bytes(data)
        e = HistoryEntry(h, time.time(), parent, metadata or {})
        with self.index.open("a") as f:
            f.write(json.dumps(e.__dict__, sort_keys=True) + "\n")
        return e

    def checkout(self, h):
        return (self.root / "objects" / h).read_bytes()

    def entries(self):
        if not self.index.exists():
            return []
        return [HistoryEntry(**json.loads(x)) for x in self.index.read_text().splitlines() if x]

    def as_of(self, timestamp: float):
        eligible = [e for e in self.entries() if e.created_at <= timestamp]
        if not eligible:
            raise KeyError("no Shape state exists at requested time")
        return max(eligible, key=lambda e: e.created_at)

    def tag(self, h: str, name: str):
        p = self.root / "tags.json"
        tags = json.loads(p.read_text()) if p.exists() else {}
        tags[name] = h
        p.write_text(json.dumps(tags, sort_keys=True))
        return h

    def resolve_tag(self, name: str):
        return json.loads((self.root / "tags.json").read_text())[name]
