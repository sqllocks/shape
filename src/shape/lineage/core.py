"""Shape-level lineage DAG and downstream blast-radius analysis."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Edge:
    source: str
    target: str
    transform: str | None = None


class LineageGraph:
    def __init__(self):
        self.nodes = {}
        self.out = {}
        self.inc = {}

    def add_shape(self, name, shape):
        self.nodes[name] = shape
        self.out.setdefault(name, set())
        self.inc.setdefault(name, set())
        return self

    def _reachable(self, start, target):
        seen = set()
        stack = [start]
        while stack:
            n = stack.pop()
            if n == target:
                return True
            if n in seen:
                continue
            seen.add(n)
            stack.extend(e.target for e in self.out.get(n, ()))
        return False

    def connect(self, source, target, transform=None):
        if source not in self.nodes or target not in self.nodes:
            raise KeyError("lineage endpoint")
        if source == target or self._reachable(target, source):
            raise ValueError("lineage cycle")
        e = Edge(source, target, transform)
        self.out[source].add(e)
        self.inc[target].add(e)
        return e

    def downstream(self, name):
        seen = set()
        stack = [name]
        while stack:
            n = stack.pop()
            for e in self.out.get(n, ()):
                if e.target not in seen:
                    seen.add(e.target)
                    stack.append(e.target)
        return tuple(sorted(seen))

    def blast_radius(self, changed):
        return {n: self.downstream(n) for n in changed}
