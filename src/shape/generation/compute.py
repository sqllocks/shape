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

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.generation.arrowkit import array as arrow_array
from shape.generation.arrowkit import fill_null as arrow_fill_null
from shape.generation.arrowkit import scalar as arrow_scalar
from shape.generation.arrowkit import to_numpy as arrow_numpy
from shape.generation.kernel_relational import group_sums
from shape.generation.keypos import dense_start, first_positions
from shape.generation.schema import GenSchema

AGGREGATES = {
    "sum_children": "sum",
    "count_children": "count",
    "avg_children": "mean",
    "min_children": "min",
    "max_children": "max",
}


def _round_if_float(arr: pa.Array) -> pa.Array:
    """Floats rounded to 2 places the way numpy does (``rint(x * 100) / 100``). Arrow's own
    ``round`` leaves a sum such as 114.49000000000001 as it is, because ``x * 100`` is already a
    whole number in floating point; the result would print with that noise."""
    if not pa.types.is_floating(arr.type):
        return arr
    mask = arrow_numpy(arr.is_null()) if arr.null_count else None
    values = np.round(np.asarray(arrow_numpy(arrow_fill_null(arr, 0))), 2)
    return arrow_array(values, type=arr.type, mask=mask)


def _bincount_aggregate(
    parent: pa.Table, child: pa.Table, pk: str, fk: str, source: str, rule: str
) -> pa.Array | None:
    """``sum`` and ``count`` of a numeric child column in one pass over the child rows (a
    row-ordered ``bincount`` per parent row, which adds in the same order as Arrow's grouped sum),
    or ``None`` for anything else. Sums of integers stay exact (the guard keeps every partial sum
    below 2**53)."""
    values = child[source].combine_chunks()
    t = values.type
    if rule not in ("sum_children", "count_children"):
        return None
    if not (pa.types.is_floating(t) or pa.types.is_integer(t)):
        return None
    n = parent.num_rows
    start = dense_start(parent[pk])
    if start is not None and child[fk].type == pa.int64():
        sums, counts = group_sums(child[fk].combine_chunks(), values.cast(t), start, n)
        if rule == "count_children":
            return counts
        return _round_if_float(sums)
    pos = first_positions(child[fk], parent[pk])
    valid = pos >= 0
    if values.null_count:
        valid &= ~np.asarray(arrow_numpy(values.is_null()), dtype=bool)
    pos = pos[valid]
    counts = np.bincount(pos, minlength=n)
    if rule == "count_children":
        return arrow_array(counts.astype(np.int64))
    flat = np.asarray(arrow_numpy(arrow_fill_null(values, 0)))[valid]
    integral = pa.types.is_integer(t)
    if integral and float(np.abs(flat.astype(np.float64)).sum()) >= 2.0**53:
        return None
    sums = np.bincount(pos, weights=flat.astype(np.float64), minlength=n)
    return _round_if_float(arrow_array(sums.astype(np.int64)) if integral else arrow_array(sums))


def _aggregate(
    parent: pa.Table, child: pa.Table, pk: str, fk: str, source: str, rule: str
) -> pa.Array:
    fast = _bincount_aggregate(parent, child, pk, fk, source, rule)
    if fast is not None:
        return fast
    func = AGGREGATES[rule]
    grouped = child.group_by(fk).aggregate([(source, func)])
    keys, values = grouped[fk].combine_chunks(), grouped[f"{source}_{func}"].combine_chunks()
    pos = pc.index_in(parent[pk].combine_chunks(), value_set=keys)
    out = pc.take(values, pos)
    return _round_if_float(arrow_fill_null(out, arrow_scalar(0, type=out.type)))


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
