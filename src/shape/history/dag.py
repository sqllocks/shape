"""Content-addressed Shape history DAG."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Revision:
    id: str
    parents: tuple[str, ...]
    payload: dict
    message: str = ""


class HistoryDAG:
    def __init__(self):
        self.revisions = {}
        self.tags = {}

    def commit(self, payload, parents=(), message=""):
        for p in parents:
            if p not in self.revisions:
                raise KeyError(p)
        raw = json.dumps(
            {"parents": parents, "payload": payload},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
        rid = hashlib.sha256(raw).hexdigest()
        self.revisions[rid] = Revision(rid, tuple(parents), payload, message)
        return rid

    def tag(self, name, rid):
        if rid not in self.revisions:
            raise KeyError(rid)
        self.tags[name] = rid

    def resolve(self, ref):
        return self.tags.get(ref, ref)

    def ancestors(self, ref):
        start = self.resolve(ref)
        seen = set()
        stack = [start]
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            if x not in self.revisions:
                raise KeyError(x)
            seen.add(x)
            stack.extend(self.revisions[x].parents)
        seen.discard(start)
        return seen
