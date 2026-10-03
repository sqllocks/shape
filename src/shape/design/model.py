"""The schema design input: entities, attributes, keys, functional dependencies, hierarchies,
history needs, and facts (declared grain, measures with additivity, dimensions).

The JSON form is described by ``shape/schemas/design-input-v1.json`` and carries ``format`` and
an integer ``version``. :meth:`DesignInput.from_dict` checks a document against that schema and
then against the rules a schema cannot express (names that exist, no duplicates); both kinds of
failure raise :class:`DesignError`. :meth:`DesignInput.to_dict` writes the canonical form, with
every default filled in.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from shape.errors import ShapeError
from shape.schemacheck import validate

DESIGN_FORMAT = "shape-design"
DESIGN_VERSION = 1
TYPES = (
    "integer",
    "string",
    "decimal",
    "timestamp",
    "boolean",
    "uuid",
    "float",
    "date",
    "time",
    "binary",
)
ADDITIVITY = ("additive", "semi_additive", "non_additive")
SCD_TYPES = (0, 1, 2, 3)


class DesignError(ShapeError):
    """A design input is invalid, or cannot be turned into a schema."""


def design_input_schema() -> dict[str, Any]:
    """The JSON Schema of the design input."""
    text = resources.files("shape").joinpath("schemas/design-input-v1.json").read_text("utf-8")
    doc: dict[str, Any] = json.loads(text)
    return doc


@dataclass(frozen=True, slots=True)
class Attribute:
    name: str
    type: str = "string"
    nullable: bool = True
    max_length: int | None = None
    precision: int | None = None
    scale: int | None = None
    references: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "type": self.type, "nullable": self.nullable}
        for k in ("max_length", "precision", "scale", "references"):
            if getattr(self, k) is not None:
                out[k] = getattr(self, k)
        return out


@dataclass(frozen=True, slots=True)
class Dependency:
    determinant: tuple[str, ...]
    dependent: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class History:
    """Slowly changing dimension types: ``default`` for every attribute, ``attributes`` per
    attribute (0 retain, 1 overwrite, 2 new row, 3 previous-value column)."""

    default: int = 1
    attributes: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class Entity:
    name: str
    attributes: tuple[Attribute, ...]
    keys: tuple[tuple[str, ...], ...] = ()
    dependencies: tuple[Dependency, ...] = ()
    history: History = field(default_factory=History)

    def attribute(self, name: str) -> Attribute:
        for a in self.attributes:
            if a.name == name:
                return a
        raise DesignError(f"entity {self.name!r}: unknown attribute {name!r}")

    @property
    def attribute_names(self) -> tuple[str, ...]:
        return tuple(a.name for a in self.attributes)


@dataclass(frozen=True, slots=True)
class Hierarchy:
    """Levels from the finest to the coarsest; each level determines the next."""

    name: str
    entity: str
    levels: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Measure:
    name: str
    attribute: str
    additivity: str | None = None
    not_additive_over: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FactDimension:
    entity: str
    via: str
    role: str | None = None


@dataclass(frozen=True, slots=True)
class Fact:
    name: str
    source: str
    grain: tuple[str, ...] = ()
    measures: tuple[Measure, ...] = ()
    dimensions: tuple[FactDimension, ...] = ()
    dates: tuple[str, ...] = ()
    degenerate: tuple[str, ...] = ()
    junk: tuple[str, ...] = ()
    many_to_many: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DesignInput:
    name: str
    entities: tuple[Entity, ...]
    hierarchies: tuple[Hierarchy, ...] = ()
    facts: tuple[Fact, ...] = ()

    def entity(self, name: str) -> Entity:
        for e in self.entities:
            if e.name == name:
                return e
        raise DesignError(f"unknown entity {name!r}")

    # ---- reading ---------------------------------------------------------------------------

    @classmethod
    def from_dict(cls, doc: Mapping[str, Any]) -> DesignInput:
        if not isinstance(doc, Mapping):
            raise DesignError("a design input must be a JSON object")
        if doc.get("format") != DESIGN_FORMAT:
            raise DesignError(f"format must be {DESIGN_FORMAT!r}, got {doc.get('format')!r}")
        version = doc.get("version")
        if isinstance(version, int) and not isinstance(version, bool):
            if version > DESIGN_VERSION:
                raise DesignError(
                    f"design input version {version} is newer than this Shape supports "
                    f"(version {DESIGN_VERSION}); upgrade Shape"
                )
            if version < 1:
                raise DesignError(f"design input version must be {DESIGN_VERSION}, got {version}")
        problems = validate(dict(doc), design_input_schema())
        if problems:
            raise DesignError("invalid design input: " + "; ".join(problems[:5]))
        entities = tuple(_entity(e) for e in doc["entities"])
        design = cls(
            name=str(doc["name"]),
            entities=entities,
            hierarchies=tuple(
                Hierarchy(h["name"], h["entity"], tuple(h["levels"]))
                for h in doc.get("hierarchies", [])
            ),
            facts=tuple(_fact(f) for f in doc.get("facts", [])),
        )
        design._check()
        return design

    def _check(self) -> None:
        _unique([e.name for e in self.entities], "entity")
        _unique([h.name for h in self.hierarchies], "hierarchy")
        _unique([f.name for f in self.facts], "fact")
        by_name = {e.name: e for e in self.entities}
        for e in self.entities:
            _unique([a.name for a in e.attributes], f"attribute in entity {e.name!r}")
            have = set(e.attribute_names)
            for k in e.keys:
                _names_exist(k, have, f"entity {e.name!r} key", nonempty=True)
            for d in e.dependencies:
                _names_exist(
                    d.determinant,
                    have,
                    f"entity {e.name!r} dependency",
                    nonempty=True,
                    what="determinant",
                )
                _names_exist(d.dependent, have, f"entity {e.name!r} dependency", nonempty=True)
            for name, _ in e.history.attributes:
                _names_exist((name,), have, f"entity {e.name!r} history")
            for a in e.attributes:
                if a.precision is not None and a.scale is not None and a.scale > a.precision:
                    raise DesignError(
                        f"entity {e.name!r} attribute {a.name!r}: scale {a.scale} is larger than "
                        f"precision {a.precision}; a decimal cannot have more fraction digits "
                        "than digits"
                    )
                if a.references is not None and a.references not in by_name:
                    raise DesignError(
                        f"entity {e.name!r} attribute {a.name!r}: unknown entity "
                        f"{a.references!r} in references"
                    )
        for h in self.hierarchies:
            ent = _known_entity(by_name, h.entity, f"hierarchy {h.name!r}")
            if len(h.levels) < 2:
                raise DesignError(f"hierarchy {h.name!r} needs at least two levels")
            _names_exist(h.levels, set(ent.attribute_names), f"hierarchy {h.name!r}")
        for f in self.facts:
            where = f"fact {f.name!r}"
            src = _known_entity(by_name, f.source, where)
            have = set(src.attribute_names)
            _names_exist(f.grain, have, f"{where} grain")
            for m in f.measures:
                _names_exist((m.attribute,), have, f"{where} measure {m.name!r}")
            _unique([m.name for m in f.measures], f"measure in {where}")
            for fd in f.dimensions:
                _known_entity(by_name, fd.entity, f"{where} dimension")
                _names_exist((fd.via,), have, f"{where} dimension {fd.entity!r}")
            for group, label in (
                (f.dates, "dates"),
                (f.degenerate, "degenerate"),
                (f.junk, "junk"),
            ):
                _names_exist(group, have, f"{where} {label}")
            for e_name in f.many_to_many:
                _known_entity(by_name, e_name, f"{where} many_to_many")

    # ---- writing ---------------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": DESIGN_FORMAT,
            "version": DESIGN_VERSION,
            "name": self.name,
            "entities": [_entity_dict(e) for e in self.entities],
            "hierarchies": [
                {"name": h.name, "entity": h.entity, "levels": list(h.levels)}
                for h in self.hierarchies
            ],
            "facts": [_fact_dict(f) for f in self.facts],
        }


def load_design(path: str | Path) -> DesignInput:
    """Read a design input file."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DesignError(f"{path}: not valid JSON ({exc})") from exc
    return DesignInput.from_dict(doc)


# ---- helpers ---------------------------------------------------------------------------------


def _unique(names: Sequence[str], what: str) -> None:
    seen: set[str] = set()
    for n in names:
        if n in seen:
            raise DesignError(f"duplicate {what} {n!r}")
        seen.add(n)


def _names_exist(
    names: Sequence[str],
    have: set[str],
    where: str,
    *,
    nonempty: bool = False,
    what: str = "list",
) -> None:
    if nonempty and not names:
        raise DesignError(f"{where}: empty {what}")
    for n in names:
        if n not in have:
            raise DesignError(f"{where}: unknown attribute {n!r}")


def _known_entity(by_name: Mapping[str, Entity], name: str, where: str) -> Entity:
    if name not in by_name:
        raise DesignError(f"{where}: unknown entity {name!r}")
    return by_name[name]


def _attribute(a: Mapping[str, Any]) -> Attribute:
    return Attribute(
        name=a["name"],
        type=a.get("type", "string"),
        nullable=a.get("nullable", True),
        max_length=a.get("max_length"),
        precision=a.get("precision"),
        scale=a.get("scale"),
        references=a.get("references"),
    )


def _entity(e: Mapping[str, Any]) -> Entity:
    h = e.get("history", {})
    return Entity(
        name=e["name"],
        attributes=tuple(_attribute(a) for a in e["attributes"]),
        keys=tuple(tuple(k) for k in e.get("keys", [])),
        dependencies=tuple(
            Dependency(tuple(d["determinant"]), tuple(d["dependent"]))
            for d in e.get("dependencies", [])
        ),
        history=History(h.get("default", 1), tuple(sorted(h.get("attributes", {}).items()))),
    )


def _fact(f: Mapping[str, Any]) -> Fact:
    return Fact(
        name=f["name"],
        source=f["source"],
        grain=tuple(f.get("grain", [])),
        measures=tuple(
            Measure(
                m["name"],
                m["attribute"],
                m.get("additivity"),
                tuple(m.get("not_additive_over", [])),
            )
            for m in f.get("measures", [])
        ),
        dimensions=tuple(
            FactDimension(d["entity"], d["via"], d.get("role")) for d in f.get("dimensions", [])
        ),
        dates=tuple(f.get("dates", [])),
        degenerate=tuple(f.get("degenerate", [])),
        junk=tuple(f.get("junk", [])),
        many_to_many=tuple(f.get("many_to_many", [])),
    )


def _entity_dict(e: Entity) -> dict[str, Any]:
    return {
        "name": e.name,
        "attributes": [a.to_dict() for a in e.attributes],
        "keys": [list(k) for k in e.keys],
        "dependencies": [
            {"determinant": list(d.determinant), "dependent": list(d.dependent)}
            for d in e.dependencies
        ],
        "history": {"default": e.history.default, "attributes": dict(e.history.attributes)},
    }


def _fact_dict(f: Fact) -> dict[str, Any]:
    measures = []
    for m in f.measures:
        item: dict[str, Any] = {"name": m.name, "attribute": m.attribute}
        if m.additivity is not None:
            item["additivity"] = m.additivity
        if m.not_additive_over:
            item["not_additive_over"] = list(m.not_additive_over)
        measures.append(item)
    dims = []
    for d in f.dimensions:
        di: dict[str, Any] = {"entity": d.entity, "via": d.via}
        if d.role is not None:
            di["role"] = d.role
        dims.append(di)
    return {
        "name": f.name,
        "source": f.source,
        "grain": list(f.grain),
        "measures": measures,
        "dimensions": dims,
        "dates": list(f.dates),
        "degenerate": list(f.degenerate),
        "junk": list(f.junk),
        "many_to_many": list(f.many_to_many),
    }
