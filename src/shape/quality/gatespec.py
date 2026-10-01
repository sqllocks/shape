"""The schema the validation gates check data against.

A gate schema is a small JSON document of Shape's own (``format: shape-gates``): tables with
typed columns, nullability, primary keys and optional distribution or enum declarations, plus
the foreign-key relationships between tables. A Shape model v2
and a profile (``shape.profile(...).to_dict()``, with the primary and foreign keys it
detected) also serve, with the nullability and types they record.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FORMAT = "shape-gates"
VERSION = 1

_KIND_TYPES = {
    "int": "integer",
    "float": "float",
    "bool": "boolean",
    "text": "string",
    "temporal": "datetime",
    "other": "string",
}


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
        return {"format": FORMAT, "version": VERSION, "tables": tables, "relationships": rels}

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
        if doc.get("version") != VERSION:
            raise GateSchemaError(f"unsupported gate schema version {doc.get('version')!r}")
        tables: dict[str, TableSpec] = {}
        raw_tables = doc.get("tables", {})
        if not isinstance(raw_tables, Mapping):
            raise GateSchemaError("`tables` must be an object")
        for tname, t in raw_tables.items():
            if not isinstance(t, Mapping):
                raise GateSchemaError(f"table {tname!r} must be an object")
            columns: dict[str, ColumnSpec] = {}
            for cname, c in (t.get("columns") or {}).items():
                if not isinstance(c, Mapping):
                    raise GateSchemaError(f"column {tname}.{cname} must be an object")
                columns[cname] = ColumnSpec(
                    cname,
                    str(c.get("type", "string")),
                    bool(c.get("nullable", False)),
                    c.get("distribution"),
                    c.get("enum"),
                )
            tables[tname] = TableSpec(tname, columns, tuple(t.get("primary_key") or ()))
        rels: list[RelationshipSpec] = []
        for r in doc.get("relationships") or ():
            try:
                rels.append(
                    RelationshipSpec(
                        str(r["name"]),
                        str(r["parent"]),
                        str(r["child"]),
                        tuple(r["parent_columns"]),
                        tuple(r["child_columns"]),
                        str(r.get("type", "one_to_many")),
                    )
                )
            except (KeyError, TypeError) as exc:
                raise GateSchemaError(f"invalid relationship {r!r}: {exc!r}") from exc
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
