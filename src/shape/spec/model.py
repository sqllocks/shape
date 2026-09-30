"""Declarative Shape-as-Code contract model."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from functools import cache
from importlib import resources
from typing import Any

from shape.schemacheck import validate as _validate_schema


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


# ---------------------------------------------------------------------------------------------
# The Shape model, version 2 (P1-09): one JSON Schema for every path that makes or reads one.
# ---------------------------------------------------------------------------------------------

MODEL_VERSION = 2


class ModelError(ValueError):
    """A document does not follow ``shape-v2.schema.json``."""


@cache
def model_schema() -> dict[str, Any]:
    """The JSON Schema of the v2 model, as shipped in ``shape/schemas``."""
    text = resources.files("shape").joinpath("schemas/shape-v2.schema.json").read_text("utf-8")
    schema: dict[str, Any] = json.loads(text)
    return schema


def model_problems(doc: Any) -> list[str]:
    """Every way ``doc`` departs from the v2 schema (empty when it conforms)."""
    if not isinstance(doc, dict):
        return [f"$: expected an object, got {type(doc).__name__}"]
    return _validate_schema(doc, model_schema())


def validate_model(doc: Any) -> dict[str, Any]:
    """``doc`` if it is a valid v2 model, else ``ModelError`` listing the first problems."""
    problems = model_problems(doc)
    if problems:
        more = f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""
        raise ModelError("; ".join(problems[:5]) + more)
    assert isinstance(doc, dict)
    return doc


def is_model(doc: Any) -> bool:
    """True for a dict that declares itself v2 (``schema_version == 2``)."""
    return isinstance(doc, dict) and doc.get("schema_version") == MODEL_VERSION
