"""The compute phase: fill ``computed`` columns from other tables once every table exists.

A ``computed`` column is generated as a placeholder (all null); this pass back-fills it. Its
generator names a ``rule``, a ``child_table`` and a ``child_column``:

* ``sum_children``, ``count_children``, ``avg_children``, ``min_children``, ``max_children``:
  aggregate ``child_column`` of the child rows that point at each row (through the child's
  foreign key to this table); rows without children get 0. Sums and counts of integer columns
  stay integer; decimals are rounded to 2 places.
* ``lookup_parent``: ``child_table`` is the *parent*; copy its ``child_column`` through this
  table's foreign key to it.

Stable interface: :func:`apply_compute_phase`.
"""

from __future__ import annotations

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.generation.schema import GenSchema

AGGREGATES = {
    "sum_children": "sum",
    "count_children": "count",
    "avg_children": "mean",
    "min_children": "min",
    "max_children": "max",
}


def _round_if_float(arr: pa.Array) -> pa.Array:
    return pc.round(arr, 2) if pa.types.is_floating(arr.type) else arr


def _aggregate(
    parent: pa.Table, child: pa.Table, pk: str, fk: str, source: str, rule: str
) -> pa.Array:
    func = AGGREGATES[rule]
    grouped = child.group_by(fk).aggregate([(source, func)])
    keys, values = grouped[fk].combine_chunks(), grouped[f"{source}_{func}"].combine_chunks()
    pos = pc.index_in(parent[pk].combine_chunks(), value_set=keys)
    out = pc.take(values, pos)
    return _round_if_float(pc.fill_null(out, pa.scalar(0, type=out.type)))


def _lookup_parent(table: pa.Table, parent: pa.Table, fk: str, pk: str, source: str) -> pa.Array:
    pos = pc.index_in(table[fk].combine_chunks(), value_set=parent[pk].combine_chunks())
    return _round_if_float(pc.take(parent[source].combine_chunks(), pos))


def apply_compute_phase(tables: dict[str, pa.Table], schema: GenSchema) -> dict[str, pa.Table]:
    """``tables`` with every ``computed`` column filled (the inputs are not changed). A column
    whose child table was not generated, or that has no foreign key linking the two, is left as
    it is."""
    out = dict(tables)
    for tname, tdef in schema.tables.items():
        if tname not in out:
            continue
        for cname, col in tdef.columns.items():
            if col.strategy != "computed":
                continue
            cfg = col.generator
            rule = str(cfg.get("rule", "sum_children"))
            other, source = str(cfg.get("child_table", "")), str(cfg.get("child_column", ""))
            if other not in out or other not in schema.tables:
                continue
            if rule == "lookup_parent":
                link = next(
                    (
                        c
                        for c in tdef.columns.values()
                        if c.fk_ref_table == other and c.fk_ref_column
                    ),
                    None,
                )
                if link is None or source not in out[other].column_names:
                    continue
                values = _lookup_parent(
                    out[tname], out[other], link.name, str(link.fk_ref_column), source
                )
            else:
                if rule not in AGGREGATES:
                    raise ValueError(f"Unknown computed rule: '{rule}'")
                if not tdef.primary_key:
                    continue
                pk = tdef.primary_key[0]
                fk = next(
                    (
                        c.name
                        for c in schema.tables[other].columns.values()
                        if c.fk_ref_table == tname
                    ),
                    None,
                )
                if fk is None or source not in out[other].column_names:
                    continue
                values = _aggregate(out[tname], out[other], pk, fk, source, rule)
            table = out[tname]
            out[tname] = table.set_column(table.column_names.index(cname), cname, values)
    return out
