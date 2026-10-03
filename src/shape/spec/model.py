"""Declarative Shape-as-Code contract model."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from functools import cache
from importlib import resources
from typing import Any

from shape import compat
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
    """A contract file: ``version`` is the format version of the file (always 1 so far), the
    revision of a contract's content belongs in ``metadata``."""

    _KNOWN = frozenset({"name", "fidelity", "fields", "metadata"})

    name: str
    version: int = 1
    fidelity: str = "gold"
    fields: tuple[FieldContract, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    # Fields a newer release wrote that this one does not know: ignored, and written back as read.
    extra: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    def validate(self) -> ShapeContract:
        if self.version < 1:
            raise ValueError("version must be >=1")
        if self.version > compat.KINDS["contract-model"].current:
            raise compat.UnsupportedVersionError(
                f"unsupported contract model version {self.version}: this Shape reads up to "
                f"version {compat.KINDS['contract-model'].current}; {compat.newer_hint(None)}",
                kind="contract-model",
                found=self.version,
                supported=compat.KINDS["contract-model"].current,
                min_shape_version=None,
            )
        if self.fidelity not in {"bronze", "silver", "gold", "platinum"}:
            raise ValueError("unknown fidelity")
        names = [f.name for f in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("duplicate field")
        if any(not n for n in names):
            raise ValueError("empty field name")
        return self

    def to_dict(self) -> dict[str, Any]:
        body = asdict(self)
        body.pop("extra")
        body.pop("version")
        out = compat.stamp("contract-model", body, version=self.version, aliases=False)
        return {**out, **{k: v for k, v in self.extra.items() if k not in out}}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=2)

    @classmethod
    def from_dict(cls, o: Mapping[str, Any]) -> ShapeContract:
        compat.check_format("contract-model", o)
        version = compat.check_readable("contract-model", o)
        unknown = compat.check_unknown("contract-model", o, cls._KNOWN)
        return cls(
            o["name"],
            version,
            o.get("fidelity", "gold"),
            tuple(FieldContract(**f) for f in o.get("fields", [])),
            o.get("metadata", {}),
            {k: o[k] for k in unknown},
        ).validate()

    @classmethod
    def from_json(cls, s: str) -> ShapeContract:
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
