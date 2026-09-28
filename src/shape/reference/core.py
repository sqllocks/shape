"""Immutable versioned reference assets with provenance and indexed lookup."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AssetRef:
    name: str
    version: str
    content_id: str


class ReferenceAssetStore:
    def __init__(self, root):
        self.root = Path(root)
        (self.root / "objects").mkdir(parents=True, exist_ok=True)
        (self.root / "refs").mkdir(exist_ok=True)

    def publish(self, name, version, records):
        raw = json.dumps(list(records), sort_keys=True, separators=(",", ":")).encode()
        h = hashlib.sha256(raw).hexdigest()
        (self.root / "objects" / h).write_bytes(raw)
        p = self.root / "refs" / name
        p.mkdir(exist_ok=True)
        f = p / version
        if f.exists() and f.read_text() != h:
            raise ValueError("asset version immutable")
        f.write_text(h)
        return AssetRef(name, version, h)

    def load(self, name, version):
        h = (self.root / "refs" / name / version).read_text()
        return json.loads((self.root / "objects" / h).read_text())

    def index(self, name, version, key):
        return {str(r[key]): r for r in self.load(name, version) if key in r}
