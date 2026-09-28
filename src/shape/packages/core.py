"""Content-addressed local package manager for versioned Shape domains/reference assets."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from shape.packs.domains import domain_from_dict, domain_to_dict, validate_domain


def _ver(v):
    return tuple(int(x) if x.isdigit() else x for x in v.replace("-", ".").split("."))


class DomainPackageStore:
    def __init__(self, root):
        self.root = Path(root)
        (self.root / "objects").mkdir(parents=True, exist_ok=True)
        (self.root / "index").mkdir(exist_ok=True)

    def publish(self, domain, assets=None):
        issues = validate_domain(domain)
        if issues:
            raise ValueError(issues)
        payload = {"domain": domain_to_dict(domain), "assets": assets or {}}
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        h = hashlib.sha256(raw).hexdigest()
        (self.root / "objects" / h).write_bytes(raw)
        p = self.root / "index" / domain.name
        p.mkdir(exist_ok=True)
        f = p / domain.version
        if f.exists() and f.read_text() != h:
            raise ValueError("immutable package version already exists")
        f.write_text(h)
        return h

    def versions(self, name):
        p = self.root / "index" / name
        return tuple(sorted((x.name for x in p.iterdir()), key=_ver)) if p.exists() else ()

    def resolve(self, name, version=None):
        versions = self.versions(name)
        if not versions:
            raise KeyError(name)
        version = version or versions[-1]
        h = (self.root / "index" / name / version).read_text()
        return json.loads((self.root / "objects" / h).read_text())

    def install(self, name, destination, version=None):
        pkg = self.resolve(name, version)
        d = Path(destination)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.domain.json").write_text(json.dumps(pkg["domain"], indent=2, sort_keys=True))
        return domain_from_dict(pkg["domain"])
