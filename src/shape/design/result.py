"""The result of a design: tables, columns, keys and foreign keys, as plain frozen records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    type: str = "string"
    nullable: bool = True
    max_length: int | None = None
    precision: int | None = None
    scale: int | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "type": self.type, "nullable": self.nullable}
        for k in ("max_length", "precision", "scale"):
            if getattr(self, k) is not None:
                out[k] = getattr(self, k)
        return out


@dataclass(frozen=True, slots=True)
class ForeignKey:
    columns: tuple[str, ...]
    ref_table: str
    ref_columns: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "columns": list(self.columns),
            "ref_table": self.ref_table,
            "ref_columns": list(self.ref_columns),
        }


@dataclass(frozen=True, slots=True)
class Table:
    """``kind`` is ``relation`` (3NF), ``dimension``, ``date``, ``junk``, ``bridge`` or ``fact``;
    ``scd_type`` is set for dimensions (0 retain, 1 overwrite, 2 new row, 3 previous value)."""

    name: str
    kind: str
    columns: tuple[Column, ...]
    primary_key: tuple[str, ...] = ()
    foreign_keys: tuple[ForeignKey, ...] = ()
    source_entity: str | None = None
    scd_type: int | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "columns": [c.to_dict() for c in self.columns],
            "primary_key": list(self.primary_key),
            "foreign_keys": [f.to_dict() for f in self.foreign_keys],
        }
        if self.source_entity is not None:
            out["source_entity"] = self.source_entity
        if self.scd_type is not None:
            out["scd_type"] = self.scd_type
        return out


@dataclass(frozen=True, slots=True)
class SchemaDesign:
    name: str
    mode: str
    tables: tuple[Table, ...]
    notes: tuple[str, ...] = ()

    def table(self, name: str) -> Table:
        for t in self.tables:
            if t.name == name:
                return t
        raise KeyError(name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "shape-design-result",
            "version": 1,
            "name": self.name,
            "mode": self.mode,
            "tables": [t.to_dict() for t in self.tables],
            "notes": list(self.notes),
        }
