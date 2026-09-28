"""Declarative Shape-as-Code contract model."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class FieldContract:
    name: str
    kind: str
    nullable: bool = True
    semantic_type: str | None = None
    sensitivity: str = "PUBLIC"
    constraints: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ShapeContract:
    name: str
    version: int = 1
    fidelity: str = "gold"
    fields: tuple[FieldContract, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self):
        if self.version < 1:
            raise ValueError("version must be >=1")
        if self.fidelity not in {"bronze", "silver", "gold", "platinum"}:
            raise ValueError("unknown fidelity")
        names = [f.name for f in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("duplicate field")
        if any(not n for n in names):
            raise ValueError("empty field name")
        return self

    def to_dict(self):
        return asdict(self)

    def to_json(self):
        return json.dumps(self.to_dict(), sort_keys=True, indent=2)

    @classmethod
    def from_dict(cls, o):
        return cls(
            o["name"],
            int(o.get("version", 1)),
            o.get("fidelity", "gold"),
            tuple(FieldContract(**f) for f in o.get("fields", [])),
            o.get("metadata", {}),
        ).validate()

    @classmethod
    def from_json(cls, s):
        return cls.from_dict(json.loads(s))
