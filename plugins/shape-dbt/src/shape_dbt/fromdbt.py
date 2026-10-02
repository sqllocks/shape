"""``shape from-dbt``: a dbt project's tables and tests as a generation schema.

The schema is built by the builder ``shape from-ddl`` uses (``shape.generation.ddl``): this module
only turns what dbt knows into the parsed tables that builder takes.

| dbt | generation schema |
|---|---|
| ``unique`` + ``not_null`` on one column, or ``dbt_utils.unique_combination_of_columns`` | primary key |
| ``not_null`` (or a ``not_null`` constraint) | ``nullable: false`` |
| ``relationships`` (``to``, ``field``) | foreign key and relationship |
| ``accepted_values`` | ``weighted_enum`` (equal weights, or a profile's frequencies) |
| contract ``data_type`` | column type, with ``max_length``, ``precision`` and ``scale`` |
| model, source and column descriptions | table descriptions, and the metadata file |

A decimal column keeps its ``precision`` and ``scale`` (the generation engine writes it as a
double; the dbt seeds sink declares ``numeric(p,s)``). Column descriptions have no place in the
generation schema document, so they go to the metadata document (:func:`metadata`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from shape.generation.ddl import (
    ParsedColumn,
    ParsedForeignKey,
    ParsedTable,
    schema_from_parsed,
)
from shape.generation.schema import GenSchema

from .project import DbtColumn, DbtProjectError, DbtRelation, ref_target

# dbt ``data_type`` (any adapter's spelling) to the SQL type the schema builder knows.
_TYPE_ALIASES: dict[str, str] = {
    **dict.fromkeys(
        ("varchar", "char", "nvarchar", "nchar", "text", "string", "character varying", "character"),
        "varchar",
    ),
    **dict.fromkeys(("json", "jsonb", "variant", "object", "array", "struct", "map"), "varchar"),
    **dict.fromkeys(("int", "integer", "int4", "int32", "mediumint"), "integer"),
    **dict.fromkeys(("bigint", "int8", "int64", "hugeint", "long"), "bigint"),
    **dict.fromkeys(("smallint", "int2", "int16", "tinyint", "int1", "int8_unsigned"), "smallint"),
    **dict.fromkeys(
        ("float", "float4", "float8", "float32", "float64", "double", "real", "double precision"),
        "float",
    ),
    **dict.fromkeys(("decimal", "numeric", "number", "bignumeric", "dec"), "decimal"),
    **dict.fromkeys(("bool", "boolean", "bit"), "boolean"),
    "date": "date",
    **dict.fromkeys(
        (
            "timestamp",
            "timestamptz",
            "timestamp_ntz",
            "timestamp_ltz",
            "timestamp_tz",
            "datetime",
            "datetime2",
            "datetimeoffset",
        ),
        "timestamp",
    ),
    "time": "time",
    **dict.fromkeys(("uuid", "uniqueidentifier"), "uuid"),
    **dict.fromkeys(("bytes", "binary", "varbinary", "blob", "bytea"), "bytea"),
}
_TYPE_SPEC = re.compile(r"^\s*([a-z_][a-z0-9_ ]*?)\s*(?:\(\s*(\d+|max)\s*(?:,\s*(\d+)\s*)?\))?\s*$")
_ZONE_WORDS = re.compile(r"\s+(?:with|without)(?:\s+local)?\s+time\s+zone|\s+unsigned", re.I)


def parse_data_type(data_type: str | None) -> tuple[str | None, int | None, int | None, int | None]:
    """``"numeric(18,2)"`` to ``("decimal", None, 18, 2)``; ``"varchar(50)"`` to ``("varchar",
    50, None, None)``. The first item is ``None`` when the type is not known (the column is then
    typed by its name and its tests)."""
    if not data_type:
        return None, None, None, None
    m = _TYPE_SPEC.match(_ZONE_WORDS.sub("", data_type.strip().lower()))
    if not m:
        return None, None, None, None
    base = _TYPE_ALIASES.get(m.group(1).strip())
    if base is None:
        return None, None, None, None
    first = None if m.group(2) in (None, "max") else int(m.group(2))
    if base == "decimal":
        scale = int(m.group(3)) if m.group(3) is not None else 0
        # NUMBER(38,0) and DECIMAL(10,0) hold whole numbers: they are integer columns
        return ("bigint", None, None, None) if first is not None and scale == 0 else (
            base,
            None,
            first,
            scale if first is not None else None,
        )
    return base, first if base == "varchar" else None, None, None


def _args(test: Any) -> Mapping[str, Any]:
    return test.args if isinstance(test.args, Mapping) else {}


def _unique_combos(rel: DbtRelation) -> list[list[str]]:
    out = []
    for t in rel.tests:
        if t.kind in ("unique_combination_of_columns", "dbt_utils.unique_combination_of_columns"):
            cols = _args(t).get("combination_of_columns")
            if isinstance(cols, list) and cols:
                out.append([str(c) for c in cols])
    return out


def _has(col: DbtColumn, kind: str) -> bool:
    return any(t.kind == kind for t in col.tests)


def select_relations(
    relations: Sequence[DbtRelation], kinds: Iterable[str] | None = None
) -> list[DbtRelation]:
    """The relations to generate: ``kinds`` (``source``, ``seed``, ``model``), or, when it is not
    given, the sources and seeds, or the models when the project has neither."""
    if kinds is not None:
        wanted = set(kinds)
        chosen = [r for r in relations if r.kind in wanted]
    else:
        chosen = [r for r in relations if r.kind in ("source", "seed")] or [
            r for r in relations if r.kind == "model"
        ]
    seen: dict[str, DbtRelation] = {}
    for r in chosen:
        if r.name in seen:
            raise DbtProjectError(
                f"two {r.kind}s are named {r.name!r} (in {seen[r.name].source_name or 'the '}"
                f"project and {r.source_name or 'the project'}); select one kind or rename one"
            )
        seen[r.name] = r
    if not seen:
        raise DbtProjectError(
            "nothing to import: the dbt input has no "
            + ("tables of kind " + ", ".join(sorted(kinds)) if kinds else "sources, seeds or models")
        )
    return list(seen.values())


def _guess_type(col: DbtColumn, rel: DbtRelation, key: bool) -> str:
    """The type of a column dbt does not give one for: a key is an integer, a name that ends in
    ``_at`` or ``_date`` is a timestamp or date, a value set is text, anything else text."""
    name = col.name.lower()
    if key or _has(col, "relationships"):
        return "integer"
    if name.endswith(("_at", "_timestamp")):
        return "timestamp"
    if name.endswith(("_date", "_on")) or name == "date":
        return "date"
    if name.startswith(("is_", "has_")):
        return "boolean"
    if any(w in name for w in ("amount", "price", "total", "cost", "revenue")):
        return "decimal"
    return "varchar"


def _parse_tables(
    rels: list[DbtRelation],
) -> tuple[list[ParsedTable], list[ParsedForeignKey], list[str]]:
    names = {r.name: r for r in rels}
    notes: list[str] = []
    tables: list[ParsedTable] = []
    fks: list[ParsedForeignKey] = []
    types: dict[tuple[str, str], str] = {}
    for rel in rels:
        combos = _unique_combos(rel)
        single = [
            c.name for c in rel.columns.values() if _has(c, "unique") and _has(c, "not_null")
        ]
        pk = single[:1] if single else (combos[0] if combos else [])
        if len(single) > 1:
            notes.append(
                f"{rel.name}: {', '.join(single)} are all unique and not null; "
                f"{single[0]} is the primary key"
            )
        pt = ParsedTable(name=rel.name, primary_key=list(pk))
        for col in rel.columns.values():
            base, length, precision, scale = parse_data_type(col.data_type)
            if base is None:
                base = _guess_type(col, rel, col.name in pk and len(pk) == 1)
                if col.data_type:
                    notes.append(
                        f"{rel.name}.{col.name}: data_type {col.data_type!r} is not known; "
                        f"typed {base}"
                    )
            types[(rel.name, col.name)] = base
            pt.columns.append(
                ParsedColumn(
                    name=col.name,
                    raw_type=col.data_type or base,
                    base_type=base,
                    max_length=length,
                    precision=precision,
                    scale=scale,
                    nullable=not _has(col, "not_null") and col.name not in pk,
                    is_primary_key=col.name in pk,
                )
            )
            for t in col.tests:
                if t.kind != "relationships":
                    continue
                target = ref_target(_args(t).get("to"))
                parent_col = _args(t).get("field")
                if target is None or target[0] not in names or not parent_col:
                    notes.append(
                        f"{rel.name}.{col.name}: relationships to {_args(t).get('to')!r} "
                        "is not a selected table; no foreign key"
                    )
                    continue
                fks.append(ParsedForeignKey(rel.name, col.name, target[0], str(parent_col)))
        tables.append(pt)
    # A key column dbt gives no type for takes its parent's type.
    for fk in fks:
        parent = types.get((fk.parent_table, fk.parent_column))
        for t in tables:
            if t.name == fk.child_table:
                for c in t.columns:
                    if c.name == fk.child_column and parent and not names[t.name].columns[
                        c.name
                    ].data_type:
                        c.base_type = c.raw_type = parent
    return tables, fks, notes


def _enum_weights(
    values: list[Any], column: str, table: str, profile: Any | None
) -> dict[str, float]:
    keys = [str(v).lower() if isinstance(v, bool) else str(v) for v in values]
    seen: dict[str, float] = {}
    if profile is not None:
        counts = _profile_counts(profile, table, column)
        seen = {k: float(counts[k]) for k in keys if k in counts and counts[k] > 0}
    if not seen:
        return {k: 1.0 / len(keys) for k in keys}
    floor = min(seen.values()) / 10.0
    raw = {k: seen.get(k, floor) for k in keys}
    total = sum(raw.values())
    return {k: v / total for k, v in raw.items()}


def _profile_counts(profile: Any, table: str, column: str) -> dict[str, float]:
    data = profile.to_dict()
    tables = data.get("tables") or {table: data}
    col = (tables.get(table) or {}).get("columns", {}).get(column) or {}
    counts = col.get("value_counts_ext") or {}
    return {str(k): float(v) for k, v in counts.items()}


def from_dbt(
    inputs: Iterable[Any] | Sequence[DbtRelation],
    *,
    kinds: Iterable[str] | None = None,
    domain: str = "custom",
    smart: bool = True,
    scale: str | None = None,
    profile: Any | None = None,
) -> tuple[GenSchema, list[Any]]:
    """``manifest.json`` / ``schema.yml`` / ``sources.yml`` files (or a project directory, or the
    relations :func:`shape_dbt.project.read_project` returned) to ``(schema, annotations)``.

    ``kinds`` picks what to generate (``source``, ``seed``, ``model``); the default is the
    sources and seeds, or the models when there are none. ``smart`` and ``scale`` are
    ``shape from-ddl``'s. ``profile`` (a :func:`shape.profile` result) supplies the frequencies
    of the ``accepted_values`` columns; without it the values get equal weights.

    The annotations are the builder's inference notes followed by ``str`` notes about what was
    not imported."""
    from .project import read_project

    items = list(inputs)
    relations = (
        items
        if items and all(isinstance(i, DbtRelation) for i in items)
        else read_project(items)
    )
    chosen = select_relations(relations, kinds)
    tables, fks, notes = _parse_tables(chosen)
    schema, annotations = schema_from_parsed(
        tables,
        fks,
        domain=domain,
        smart=smart,
        scale=scale,
        description="Imported from a dbt project",
        name_suffix="dbt_import",
    )
    for rel in chosen:
        table = schema.tables.get(rel.name)
        if table is None:
            continue
        table.description = rel.description
        for cname, col in rel.columns.items():
            target = table.columns.get(cname)
            if target is None:
                continue
            for t in col.tests:
                if t.kind != "accepted_values":
                    continue
                values = _args(t).get("values")
                if isinstance(values, list) and values:
                    target.generator = {
                        "strategy": "weighted_enum",
                        "values": _enum_weights(values, cname, rel.name, profile),
                    }
    return schema, [*annotations, *notes]


def metadata(relations: Sequence[DbtRelation], schema: GenSchema) -> dict[str, Any]:
    """The metadata document that goes with the schema: per table its dbt kind, description and,
    per column, the description, the declared ``data_type`` and the tests dbt had. The
    generation schema has no place for column descriptions."""
    tables: dict[str, Any] = {}
    for rel in relations:
        if rel.name not in schema.tables:
            continue
        tables[rel.name] = {
            "dbt_kind": rel.kind,
            "source": rel.source_name,
            "description": rel.description,
            "columns": {
                c.name: {
                    "description": c.description,
                    "data_type": c.data_type,
                    "tests": [t.kind for t in c.tests],
                }
                for c in rel.columns.values()
                if c.name in schema.tables[rel.name].columns
            },
        }
    return {"format": "shape-dbt-metadata", "version": 1, "tables": tables}
