"""The schema the validation gates check data against.

A gate schema is a small JSON document of Shape's own (``format: shape-gates``): tables with
typed columns, nullability, primary keys and optional distribution or enum declarations, plus
the foreign-key relationships between tables. A Shape model v2
and a profile (``shape.profile(...).to_dict()``, with the primary and foreign keys it
detected) also serve, with the nullability and types they record.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shape import compat

FORMAT = "shape-gates"
VERSION = compat.KINDS["gate-schema"].current

_KIND_TYPES = {
    "int": "integer",
    "float": "float",
    "bool": "boolean",
    "text": "string",
    "temporal": "datetime",
    "other": "string",
}


_RELATIONSHIP_KEYS = ("name", "parent", "child", "parent_columns", "child_columns")


def _names(where: str, value: Any, needed: bool = False) -> tuple[str, ...]:
    """A list of column names (a bare string is refused: it would read as its characters)."""
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise GateSchemaError(f"{where} must be a list of column names, not {value!r}")
    if needed and not value:
        raise GateSchemaError(f"{where} must name at least one column")
    return tuple(value)


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _column(where: str, name: str, c: Mapping[str, Any]) -> ColumnSpec:
    """One column of a gate schema, every value checked (never coerced)."""
    typ = c.get("type", "string")
    if not isinstance(typ, str):
        raise GateSchemaError(f"column {where}: `type` must be a text, not {typ!r}")
    nullable = c.get("nullable", False)
    if not isinstance(nullable, bool):
        raise GateSchemaError(f"column {where}: `nullable` must be true or false, not {nullable!r}")
    dist = c.get("distribution")
    if dist is not None and (
        not isinstance(dist, Mapping)
        or not isinstance(dist.get("name"), str)
        or not isinstance(dist.get("params", {}), Mapping)
    ):
        raise GateSchemaError(
            f'column {where}: `distribution` must be {{"name": <scipy.stats name>, '
            f'"params": {{...}}}}, not {dist!r}'
        )
    enum = c.get("enum")
    if enum is not None and (
        not isinstance(enum, Mapping) or not all(_number(w) and w >= 0 for w in enum.values())
    ):
        raise GateSchemaError(
            f"column {where}: `enum` must map each value to a weight of 0 or more, not {enum!r}"
        )
    return ColumnSpec(name, typ, nullable, dist, enum)


class GateSchemaError(ValueError):
    """A document is not a usable gate schema."""


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    """One column. ``distribution`` is ``{"name": <scipy.stats name>, "params": {...}}`` and
    ``enum`` maps each expected value to its weight."""

    name: str
    type: str = "string"
    nullable: bool = False
    distribution: Mapping[str, Any] | None = None
    enum: Mapping[str, float] | None = None


@dataclass(frozen=True, slots=True)
class TableSpec:
    name: str
    columns: Mapping[str, ColumnSpec] = field(default_factory=dict)
    primary_key: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RelationshipSpec:
    """``child_columns`` reference ``parent_columns``. A ``self_referencing`` relationship is
    not checked."""

    name: str
    parent: str
    child: str
    parent_columns: tuple[str, ...]
    child_columns: tuple[str, ...]
    type: str = "one_to_many"


@dataclass(frozen=True, slots=True)
class GateSchema:
    tables: Mapping[str, TableSpec] = field(default_factory=dict)
    relationships: tuple[RelationshipSpec, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        tables: dict[str, Any] = {}
        for t in self.tables.values():
            cols: dict[str, Any] = {}
            for c in t.columns.values():
                col: dict[str, Any] = {"type": c.type, "nullable": c.nullable}
                if c.distribution is not None:
                    col["distribution"] = dict(c.distribution)
                if c.enum is not None:
                    col["enum"] = dict(c.enum)
                cols[c.name] = col
            tables[t.name] = {"primary_key": list(t.primary_key), "columns": cols}
        rels = [
            {
                "name": r.name,
                "type": r.type,
                "parent": r.parent,
                "child": r.child,
                "parent_columns": list(r.parent_columns),
                "child_columns": list(r.child_columns),
            }
            for r in self.relationships
        ]
        return compat.stamp("gate-schema", {"tables": tables, "relationships": rels}, aliases=False)

    @classmethod
    def from_dict(cls, doc: Mapping[str, Any]) -> GateSchema:
        if not isinstance(doc, Mapping):
            raise GateSchemaError("a gate schema must be a JSON object")
        if doc.get("schema_version") == 2:
            return cls.from_model(doc)
        if doc.get("format") is None and ("columns" in doc or "tables" in doc):
            return cls.from_profile(doc)
        if doc.get("format") != FORMAT:
            raise GateSchemaError(f"not a gate schema: expected format {FORMAT!r}")
        compat.check_readable("gate-schema", doc, error=GateSchemaError)
        tables: dict[str, TableSpec] = {}
        raw_tables = doc.get("tables", {})
        if not isinstance(raw_tables, Mapping):
            raise GateSchemaError("`tables` must be an object")
        for tname, t in raw_tables.items():
            if not isinstance(t, Mapping):
                raise GateSchemaError(f"table {tname!r} must be an object")
            columns: dict[str, ColumnSpec] = {}
            raw_columns = t.get("columns") or {}
            if not isinstance(raw_columns, Mapping):
                raise GateSchemaError(f"table {tname!r}: `columns` must be an object")
            for cname, c in raw_columns.items():
                if not isinstance(c, Mapping):
                    raise GateSchemaError(f"column {tname}.{cname} must be an object")
                columns[cname] = _column(f"{tname}.{cname}", cname, c)
            primary_key = _names(f"table {tname!r}: `primary_key`", t.get("primary_key") or [])
            tables[tname] = TableSpec(tname, columns, primary_key)
        rels: list[RelationshipSpec] = []
        for i, r in enumerate(doc.get("relationships") or ()):
            if not isinstance(r, Mapping):
                raise GateSchemaError(f"relationships[{i}]: must be an object, not {r!r}")
            for key in _RELATIONSHIP_KEYS:
                if key not in r:
                    raise GateSchemaError(
                        f'relationships[{i}]: missing required key "{key}" (a {FORMAT} '
                        f"relationship needs {', '.join(_RELATIONSHIP_KEYS)}; got {sorted(r)})"
                    )
            try:
                rels.append(
                    RelationshipSpec(
                        str(r["name"]),
                        str(r["parent"]),
                        str(r["child"]),
                        _names(f"relationships[{i}]: `parent_columns`", r["parent_columns"], True),
                        _names(f"relationships[{i}]: `child_columns`", r["child_columns"], True),
                        str(r.get("type", "one_to_many")),
                    )
                )
            except TypeError as exc:
                raise GateSchemaError(f"relationships[{i}]: invalid value: {exc}") from exc
        return cls(tables, tuple(rels))

    @classmethod
    def from_profile(cls, doc: Mapping[str, Any]) -> GateSchema:
        """The gate schema a profile implies (``Profile.to_dict()``, one table or a dataset):
        a column is nullable when the profile saw nulls in it, its type is the profiled
        ``dtype``, and the primary keys and the dataset's detected foreign keys carry over."""
        raw_tables = doc["tables"] if "tables" in doc else {doc.get("name") or "table": doc}
        tables: dict[str, TableSpec] = {}
        for tname, t in raw_tables.items():
            columns = {
                cname: ColumnSpec(cname, str(c.get("dtype", "string")), bool(c.get("null_count")))
                for cname, c in t["columns"].items()
            }
            tables[tname] = TableSpec(tname, columns, tuple(t.get("primary_key") or ()))
        rels = tuple(
            RelationshipSpec(
                str(r["name"]),
                str(r["parent"]),
                str(r["child"]),
                tuple(r["parent_columns"]),
                tuple(r["child_columns"]),
                str(r.get("type", "one_to_many")),
            )
            for r in doc.get("relationships") or ()
        )
        return cls(tables, rels)

    @classmethod
    def from_model(cls, model: Mapping[str, Any]) -> GateSchema:
        """The gate schema a Shape model v2 implies: a column is nullable when the profile saw
        nulls in it, its type follows its kind, and primary keys carry over. A profile records
        no foreign keys, so there are no relationships to check."""
        tables: dict[str, TableSpec] = {}
        for tname, t in model["tables"].items():
            columns = {
                c["name"]: ColumnSpec(
                    c["name"],
                    _KIND_TYPES.get(c.get("kind"), "string"),
                    bool(c.get("null_count")),
                )
                for c in t["columns"]
            }
            tables[tname] = TableSpec(tname, columns, tuple(t.get("primary_key") or ()))
        return cls(tables)


def load_gate_schema(path: str | Path) -> GateSchema:
    """A gate schema from a JSON file (a gate schema or a Shape model v2) or a ``.shape``
    profile artifact."""
    p = Path(path)
    if p.suffix == ".shape":
        import shape

        doc: Any = shape.load(str(p)).to_dict()
    else:
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise GateSchemaError(f"{p} is not valid JSON: {exc}") from exc
    return GateSchema.from_dict(doc)
