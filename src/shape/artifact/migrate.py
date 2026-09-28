"""Deterministic .shape format migration registry."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Migration:
    source: int
    target: int
    name: str
    fn: Callable[[dict, dict], tuple[dict, dict]]


class MigrationRegistry:
    def __init__(self):
        self._m = {}

    def register(self, source, target, name, fn):
        if target != source + 1:
            raise ValueError("migrations must advance exactly one format version")
        if source in self._m:
            raise ValueError(f"migration already registered from {source}")
        self._m[source] = Migration(source, target, name, fn)
        return fn

    def path(self, source, target):
        if target < source:
            raise ValueError("downgrade is not supported")
        out = []
        v = source
        while v < target:
            if v not in self._m:
                raise KeyError(f"no migration from format {v}")
            m = self._m[v]
            out.append(m)
            v = m.target
        return tuple(out)

    def migrate(self, manifest, shape, target):
        m = dict(manifest)
        s = dict(shape)
        applied = []
        for x in self.path(int(m.get("format_version", 1)), target):
            m, s = x.fn(m, s)
            m["format_version"] = x.target
            applied.append(x.name)
        return m, s, tuple(applied)


MIGRATIONS = MigrationRegistry()
