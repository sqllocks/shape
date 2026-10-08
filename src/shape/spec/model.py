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

FIDELITIES = ("bronze", "silver", "gold", "platinum")
_CAPABILITY_KEYS = ("mandatory_capabilities", "optional_capabilities")


@cache
def contract_schema() -> dict[str, Any]:
    """The JSON Schema of a Shape-as-Code v1 contract, as shipped in ``shape/schemas``."""
    text = resources.files("shape").joinpath("schemas/shape-v1.schema.json").read_text("utf-8")
    schema: dict[str, Any] = json.loads(text)
    return schema


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

    _KNOWN = frozenset({"name", "fidelity", "fields", "metadata", *_CAPABILITY_KEYS})

    name: str
    version: int = 1
    fidelity: str = "gold"
    fields: tuple[FieldContract, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    # Fields a newer release wrote that this one does not know: ignored, and written back as read.
    extra: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    def validate(self) -> ShapeContract:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("$.name: a contract needs a non-empty name")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError(f"$.version: must be an integer of 1 or more, got {self.version!r}")
        if self.version > compat.KINDS["contract-model"].current:
            raise compat.UnsupportedVersionError(
                f"unsupported contract model version {self.version}: this Shape reads up to "
                f"version {compat.KINDS['contract-model'].current}; {compat.newer_hint(None)} "
                "(upgrade Shape)",
                kind="contract-model",
                found=self.version,
                supported=compat.KINDS["contract-model"].current,
                min_shape_version=None,
            )
        if self.fidelity not in FIDELITIES:
            raise ValueError(
                f"$.fidelity: unknown fidelity {self.fidelity!r}; choose one of "
                f"{', '.join(FIDELITIES)}"
            )
        seen: set[str] = set()
        for i, f in enumerate(self.fields):
            if not f.name:
                raise ValueError(f"$.fields[{i}].name: empty field name")
            if f.name in seen:
                raise ValueError(f"$.fields[{i}].name: duplicate field {f.name!r}")
            seen.add(f.name)
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
        """The contract in ``o``, checked against ``shape-v1.schema.json`` (``version``,
        ``fidelity`` and ``fields`` default to 1, ``gold`` and none) and against the mandatory
        capabilities this Shape supports: an unknown one is refused (SHAPE_1_0 rule 1).

        Unknown fields follow ``docs/specs/STATE_AND_COMPATIBILITY.md``: a file a Shape release
        wrote (it declares ``format``) may carry fields a newer release added, which are ignored
        and written back as read (strict mode refuses them); a contract written by hand keeps its
        typo protection, so an unknown field is refused unless its name starts with ``x_``."""
        if not isinstance(o, Mapping):
            raise ValueError(f"$: a contract must be a JSON object, got {type(o).__name__}")
        if "name" not in o:
            raise ValueError("$.name: a contract needs a name")
        compat.check_format("contract-model", o)
        given = o.get("version", 1)
        if isinstance(given, bool) or not isinstance(given, int):
            raise compat.FormatError(f"$.version: must be an integer of 1 or more, got {given!r}")
        version = compat.check_readable("contract-model", o)
        unknown = compat.check_unknown("contract-model", o, cls._KNOWN)
        if compat.FORMAT_KEY not in o:
            typos = [k for k in unknown if not k.startswith("x_")]
            if typos:
                raise ValueError(
                    f"$.{typos[0]}: unknown field; a field of your own starts with 'x_'"
                )
        known = {k: v for k, v in o.items() if k in cls._KNOWN}
        doc = {"version": version, "fidelity": "gold", "fields": [], **known}
        problems = _validate_schema(doc, contract_schema())
        if problems:
            more = f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""
            raise ValueError("invalid contract: " + "; ".join(problems[:5]) + more)
        from .capabilities import check_capabilities

        caps = check_capabilities(doc)  # type: ignore[no-untyped-call]
        if not caps.compatible:
            raise ValueError(
                "$.mandatory_capabilities: this Shape does not support "
                f"{', '.join(caps.unknown_mandatory)}; upgrade Shape or drop the capability"
            )
        # the capabilities are no field of the dataclass: kept with the unknown fields, as read
        kept = [*unknown, *(k for k in _CAPABILITY_KEYS if k in o)]
        return cls(
            doc["name"],
            doc["version"],
            doc["fidelity"],
            tuple(FieldContract(**f) for f in doc["fields"]),
            doc.get("metadata", {}),
            {k: o[k] for k in kept},
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
