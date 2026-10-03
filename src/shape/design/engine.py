"""The deterministic engine: a :class:`~shape.design.model.DesignInput` to a 3NF, star or snowflake
schema (:class:`~shape.design.result.SchemaDesign`).

3NF: per entity, Bernstein synthesis over its dependencies and declared keys (lossless join,
dependency preserving), a foreign key from each reference attribute to the key of the entity it
names, and a foreign key between the relations of one entity wherever one holds another's key.

Star and snowflake, by Kimball's rules: one fact table per declared fact at its declared grain
(a surrogate-key column per dimension or role, a date key per date, degenerate dimensions kept
on the fact, low-cardinality flags in a junk dimension, many-to-many through a bridge table),
one conformed dimension per entity shared by every fact that uses it, surrogate keys with the
natural key kept, slowly changing dimension columns per the history needs, and a date
dimension. A snowflake additionally moves each hierarchy level above the finest into its own
outrigger table, chained by surrogate keys.

Nothing here depends on iteration order of sets or on the clock: the same input gives the same
tables, in the same order.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from shape.design.fd import FD, candidate_keys, closure, synthesize_3nf
from shape.design.model import Attribute, DesignError, DesignInput, Entity, Hierarchy
from shape.design.result import Column, ForeignKey, SchemaDesign, Table
from shape.generation.ddl_names import snake

MODES = ("3nf", "star", "snowflake")
_KIND_ORDER = {"relation": 0, "dimension": 0, "date": 1, "junk": 2, "bridge": 3, "fact": 4}


def derive(design: DesignInput, mode: str = "3nf") -> SchemaDesign:
    """The schema for ``design`` in ``mode`` (``3nf``, ``star`` or ``snowflake``)."""
    if mode not in MODES:
        raise DesignError(f"unknown mode {mode!r}; choose one of {', '.join(MODES)}")
    try:
        if mode == "3nf":
            return _third_normal_form(design)
        return _dimensional(design, mode)
    except ValueError as exc:
        if isinstance(exc, DesignError):
            raise
        raise DesignError(str(exc)) from exc


# ---- shared helpers --------------------------------------------------------------------------


def _column(a: Attribute, *, nullable: bool | None = None, name: str | None = None) -> Column:
    return Column(
        name or a.name,
        a.type,
        a.nullable if nullable is None else nullable,
        a.max_length,
        a.precision,
        a.scale,
    )


def entity_fds(entity: Entity) -> list[FD]:
    """The entity's declared dependencies plus ``key -> every other attribute`` per key."""
    fds = [FD(d.determinant, d.dependent) for d in entity.dependencies]
    for k in entity.keys:
        rest = tuple(a for a in entity.attribute_names if a not in k)
        if rest:
            fds.append(FD(k, rest))
    return fds


def primary_key(entity: Entity, fds: Sequence[FD]) -> tuple[str, ...]:
    """The first declared key, else the first candidate key found from the dependencies."""
    if entity.keys:
        return entity.keys[0]
    return candidate_keys(entity.attribute_names, fds)[0]


def _check_unique(names: Sequence[str], what: str) -> None:
    seen: set[str] = set()
    for n in names:
        if n in seen:
            raise DesignError(f"{what}: duplicate name {n!r}")
        seen.add(n)


# ---- 3NF -------------------------------------------------------------------------------------


def _third_normal_form(design: DesignInput) -> SchemaDesign:
    by_entity: dict[str, list[tuple[str, tuple[str, ...], tuple[tuple[str, ...], ...]]]] = {}
    pks: dict[str, tuple[str, ...]] = {}
    base_table: dict[str, str] = {}
    for ent in design.entities:
        fds = entity_fds(ent)
        pk = primary_key(ent, fds)
        pks[ent.name] = pk
        rels = synthesize_3nf(ent.attribute_names, fds)
        base_index = next((i for i, r in enumerate(rels) if set(pk) <= set(r.attrs)), 0)
        items: list[tuple[str, tuple[str, ...], tuple[tuple[str, ...], ...]]] = []
        for i, r in enumerate(rels):
            if i == base_index:
                name = snake(ent.name)
            else:
                name = f"{snake(ent.name)}_{'_'.join(snake(a) for a in r.keys[0])}"
            items.append((name, r.attrs, r.keys))
        by_entity[ent.name] = items
        base_table[ent.name] = items[base_index][0]
    tables: list[Table] = []
    for ent in design.entities:
        items = by_entity[ent.name]
        base_name = base_table[ent.name]
        primary: dict[str, tuple[str, ...]] = {}
        for name, attrs, keys in items:
            pk = pks[ent.name]
            primary[name] = pk if name == base_name and set(pk) <= set(attrs) else keys[0]
        for name, attrs, keys in items:
            fks: list[ForeignKey] = []
            for other, _attrs, _keys in items:
                if other == name:
                    continue
                k = primary[other]
                if set(k) <= set(attrs) and k not in keys:
                    fks.append(ForeignKey(k, other, k))
            for a in attrs:
                attr = ent.attribute(a)
                if attr.references is None:
                    continue
                target = design.entity(attr.references)
                target_pk = pks[target.name]
                if len(target_pk) != 1:
                    raise DesignError(
                        f"entity {ent.name!r} attribute {a!r} references {target.name!r}, whose "
                        f"key has {len(target_pk)} columns; a reference needs a one-column key"
                    )
                if base_table[target.name] == name:
                    continue
                fks.append(ForeignKey((a,), base_table[target.name], target_pk))
            cols = tuple(
                _column(ent.attribute(a), nullable=False if a in primary[name] else None)
                for a in attrs
            )
            tables.append(
                Table(
                    name,
                    "relation",
                    cols,
                    primary[name],
                    tuple(fks),
                    source_entity=ent.name,
                )
            )
    _check_unique([t.name for t in tables], "tables")
    return SchemaDesign(design.name, "3nf", tuple(tables))


# ---- star and snowflake ----------------------------------------------------------------------


def _scd(entity: Entity, attr: str) -> int:
    return dict(entity.history.attributes).get(attr, entity.history.default)


def _history_columns(entity: Entity, attrs: Sequence[str], keys: Sequence[str]) -> list[Column]:
    types = {a: _scd(entity, a) for a in attrs if a not in keys}
    out = [
        _column(entity.attribute(a), nullable=True, name=f"previous_{a}")
        for a in attrs
        if types.get(a) == 3
    ]
    if any(t == 2 for t in types.values()):
        out += [
            Column("valid_from", "date", False),
            Column("valid_to", "date", True),
            Column("is_current", "boolean", False),
        ]
    return out


def _table_scd(entity: Entity, attrs: Sequence[str], keys: Sequence[str]) -> int:
    types = [_scd(entity, a) for a in attrs if a not in keys]
    if 2 in types:
        return 2
    if 3 in types:
        return 3
    return 0 if types and all(t == 0 for t in types) else 1


def _surrogate(name: str) -> Column:
    return Column(name, "integer", False)


def _date_dimension() -> Table:
    # The columns of the star transform's date dimension, so the two cannot drift apart.
    from shape.dimensional.star import build_date_dimension

    schema = build_date_dimension(dt.date(2000, 1, 1), dt.date(2000, 1, 1)).schema
    cols = tuple(Column(f.name, _logical(f.type), False) for f in schema)
    return Table("dim_date", "date", cols, (cols[0].name,))


def _logical(arrow_type: object) -> str:
    import pyarrow.types as pat  # type: ignore[import-untyped]

    if pat.is_boolean(arrow_type):
        return "boolean"
    if pat.is_integer(arrow_type):
        return "integer"
    if pat.is_floating(arrow_type):
        return "float"
    if pat.is_date(arrow_type):
        return "date"
    if pat.is_timestamp(arrow_type):
        return "timestamp"
    return "string"


def _outriggers(
    design: DesignInput, entity: Entity, nk: tuple[str, ...]
) -> list[tuple[str, list[str], str | None]]:
    """The hierarchy levels of ``entity`` to move out of its dimension, coarsest first: the
    level attribute, the attributes it carries (those it determines), and the level above."""
    fds = entity_fds(entity)
    hierarchies = [h for h in design.hierarchies if h.entity == entity.name]
    for h in hierarchies:
        fds += [FD((a,), (b,)) for a, b in zip(h.levels, h.levels[1:], strict=False)]
    assigned: set[str] = set(nk)
    found: list[tuple[str, list[str], str | None]] = []
    for h in hierarchies:
        levels = _realized_levels(h, assigned)
        above: str | None = None
        batch: list[tuple[str, list[str], str | None]] = []
        for lvl in reversed(levels):
            finer = set(h.levels[: h.levels.index(lvl)])
            carried = closure((lvl,), fds) - assigned - finer
            members = [a for a in entity.attribute_names if a in carried]
            assigned.update(members)
            batch.append((lvl, members, above))
            above = lvl
        found += batch
    return found


def _realized_levels(h: Hierarchy, assigned: set[str]) -> list[str]:
    """Levels above the finest that are free to move out (not a key, not already moved)."""
    return [lvl for lvl in h.levels[1:] if lvl not in assigned]


def _dimension_tables(design: DesignInput, entity: Entity, snowflake: bool) -> list[Table]:
    fds = entity_fds(entity)
    nk = primary_key(entity, fds)
    base_sk = f"sk_{snake(entity.name)}"
    base_name = f"dim_{snake(entity.name)}"
    moved: list[tuple[str, list[str], str | None]] = (
        _outriggers(design, entity, nk) if snowflake else []
    )
    moved_attrs = {a for _, members, _ in moved for a in members}
    out: list[Table] = []
    # Outriggers: each holds its attributes, then a key to the level above, then its history.
    for lvl, members, above in moved:
        sk = f"sk_{snake(entity.name)}_{snake(lvl)}"
        cols = [_surrogate(sk)] + [_column(entity.attribute(a)) for a in members]
        fks: list[ForeignKey] = []
        if above is not None:
            up = f"sk_{snake(entity.name)}_{snake(above)}"
            cols.append(Column(up, "integer", True))
            fks.append(ForeignKey((up,), f"{base_name}_{snake(above)}", (up,)))
        cols += _history_columns(entity, members, nk)
        out.append(
            Table(
                f"{base_name}_{snake(lvl)}",
                "dimension",
                tuple(cols),
                (sk,),
                tuple(fks),
                entity.name,
                _table_scd(entity, members, nk),
            )
        )
    base_attrs = [a for a in nk] + [
        a for a in entity.attribute_names if a not in nk and a not in moved_attrs
    ]
    cols = [_surrogate(base_sk)]
    cols += [_column(entity.attribute(a), nullable=False if a in nk else None) for a in base_attrs]
    base_fks: list[ForeignKey] = []
    # The base dimension links to the finest moved level of each hierarchy.
    linked = {above for _, _, above in moved if above is not None}
    for lvl, _, _ in reversed(moved):
        if lvl in linked:
            continue
        fk_col = f"sk_{snake(entity.name)}_{snake(lvl)}"
        cols.append(Column(fk_col, "integer", True))
        base_fks.append(ForeignKey((fk_col,), f"{base_name}_{snake(lvl)}", (fk_col,)))
    cols += _history_columns(entity, base_attrs, nk)
    out.append(
        Table(
            base_name,
            "dimension",
            tuple(cols),
            (base_sk,),
            tuple(base_fks),
            entity.name,
            _table_scd(entity, base_attrs, nk),
        )
    )
    return out


def _dimensional(design: DesignInput, mode: str) -> SchemaDesign:
    if not design.facts:
        raise DesignError(f"mode {mode!r} needs facts, but the design has no facts")
    snowflake = mode == "snowflake"
    dims: dict[str, list[Table]] = {}
    tables: dict[str, Table] = {}
    used_by: dict[str, list[str]] = {}

    def ensure(entity_name: str, fact: str) -> str:
        """The base dimension table for ``entity_name``, built once and shared (conformed)."""
        if entity_name not in dims:
            ent = design.entity(entity_name)
            dims[entity_name] = _dimension_tables(design, ent, snowflake)
            for t in dims[entity_name]:
                tables[t.name] = t
        name = f"dim_{snake(entity_name)}"
        used_by.setdefault(name, [])
        if fact not in used_by[name]:
            used_by[name].append(fact)
        return name

    date_needed = False
    for f in design.facts:
        src = design.entity(f.source)
        if not f.grain:
            raise DesignError(f"fact {f.name!r} has no declared grain")
        fds = entity_fds(src)
        determined = closure(f.grain, fds)
        needed = (
            [m.attribute for m in f.measures]
            + [d.via for d in f.dimensions]
            + list(f.dates)
            + list(f.junk)
        )
        for a in needed:
            if a not in determined:
                raise DesignError(
                    f"fact {f.name!r}: the grain ({', '.join(f.grain)}) does not determine "
                    f"attribute {a!r} of {src.name!r}; declare a finer grain or a dependency"
                )
        cols: list[Column] = []
        fks: list[ForeignKey] = []
        column_for: dict[str, str] = {}
        for d in f.dimensions:
            dim = ensure(d.entity, f.name)
            tnk = _dimension_key(design.entity(d.entity))
            col = f"sk_{snake(d.role or d.entity)}"
            cols.append(_surrogate(col))
            fks.append(ForeignKey((col,), dim, (tnk,)))
            column_for.setdefault(d.via, col)
        for a in f.dates:
            date_needed = True
            col = f"sk_{snake(a)}"
            cols.append(_surrogate(col))
            fks.append(ForeignKey((col,), "dim_date", ("sk_date",)))
            column_for.setdefault(a, col)
        if f.junk:
            junk_name = f"dim_{snake(f.name)}_junk"
            jcols = [_surrogate("sk_junk")] + [_column(src.attribute(a)) for a in f.junk]
            tables[junk_name] = Table(junk_name, "junk", tuple(jcols), ("sk_junk",), (), src.name)
            cols.append(_surrogate("sk_junk"))
            fks.append(ForeignKey(("sk_junk",), junk_name, ("sk_junk",)))
        for e_name in f.many_to_many:
            dim = ensure(e_name, f.name)
            ent = design.entity(e_name)
            group = f"sk_{snake(e_name)}_group"
            member = f"sk_{snake(e_name)}"
            bridge = f"bridge_{snake(f.name)}_{snake(e_name)}"
            tables[bridge] = Table(
                bridge,
                "bridge",
                (
                    _surrogate(group),
                    _surrogate(member),
                    Column("weighting_factor", "decimal", True, None, 9, 6),
                ),
                (group, member),
                (ForeignKey((member,), dim, (_dimension_key(ent),)),),
                ent.name,
            )
            cols.append(_surrogate(group))
        for a in f.degenerate:
            cols.append(_column(src.attribute(a)))
            column_for.setdefault(a, a)
        for m in f.measures:
            cols.append(_column(src.attribute(m.attribute), name=m.name))
        pk: list[str] = []
        for a in f.grain:
            if a not in column_for:
                raise DesignError(
                    f"fact {f.name!r}: grain attribute {a!r} is not a dimension reference, a "
                    f"date or a degenerate dimension, so it has no column in the fact table"
                )
            if column_for[a] not in pk:
                pk.append(column_for[a])
        names = [c.name for c in cols]
        _check_unique(names, f"fact {f.name!r} columns")
        fcols = tuple(
            Column(c.name, c.type, False, c.max_length, c.precision, c.scale) if c.name in pk else c
            for c in cols
        )
        fact_name = f"fact_{snake(f.name)}"
        tables[fact_name] = Table(fact_name, "fact", fcols, tuple(pk), tuple(fks), src.name)
    if date_needed:
        tables["dim_date"] = _date_dimension()
    notes = tuple(
        f"conformed dimension {name}: shared by facts {', '.join(facts)}"
        for name, facts in used_by.items()
        if len(facts) > 1
    )
    _check_unique([t.name for t in tables.values()], "tables")
    ordered = sorted(enumerate(tables.values()), key=lambda it: (_KIND_ORDER[it[1].kind], it[0]))
    return SchemaDesign(design.name, mode, tuple(t for _, t in ordered), notes)


def _dimension_key(entity: Entity) -> str:
    return f"sk_{snake(entity.name)}"


__all__ = ["MODES", "derive", "entity_fds", "primary_key"]
