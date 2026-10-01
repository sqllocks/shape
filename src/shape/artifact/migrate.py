"""Deterministic .shape format migration registry."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_Fn = Callable[[dict[str, Any], dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class Migration:
    source: int
    target: int
    name: str
    fn: _Fn


class MigrationRegistry:
    def __init__(self) -> None:
        self._m: dict[int, Migration] = {}

    def register(self, source: int, target: int, name: str, fn: _Fn) -> _Fn:
        if target != source + 1:
            raise ValueError("migrations must advance exactly one format version")
        if source in self._m:
            raise ValueError(f"migration already registered from {source}")
        self._m[source] = Migration(source, target, name, fn)
        return fn

    def path(self, source: int, target: int) -> tuple[Migration, ...]:
        if target < source:
            raise ValueError("downgrade is not supported")
        out: list[Migration] = []
        v = source
        while v < target:
            if v not in self._m:
                raise KeyError(f"no migration from format {v}")
            m = self._m[v]
            out.append(m)
            v = m.target
        return tuple(out)

    def migrate(
        self, manifest: dict[str, Any], shape: dict[str, Any], target: int
    ) -> tuple[dict[str, Any], dict[str, Any], tuple[str, ...]]:
        m = dict(manifest)
        s = dict(shape)
        applied: list[str] = []
        for x in self.path(int(m.get("format_version", 1)), target):
            m, s = x.fn(m, s)
            m["format_version"] = x.target
            applied.append(x.name)
        return m, s, tuple(applied)


MIGRATIONS = MigrationRegistry()
