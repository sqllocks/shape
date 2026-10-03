"""Defects planted into generated tables, deterministically: the data side of a scenario.

Each defect is a mapping with a ``kind`` and a ``table``; the rows it touches are drawn with a
generator seeded from the run seed and the defect's position, so a rerun plants the same defects.
Every function returns the table and the number of rows it changed.

``inject_nulls``      ``column``, ``fraction``: that share of the rows get a null in the column.
``duplicate_keys``    ``column`` (default: the single-column primary key), ``fraction``: that
                      share of the rows take the key of another row.
``orphan_keys``       ``column``, ``fraction``: that share of the rows get a key no parent has.
``late_arrivals``     ``column``, ``fraction``, ``days``: that share of the rows get a timestamp
                      ``days`` earlier, so they arrive behind newer data.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Mapping
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.errors import ShapeError
from shape.generation.schema import GenSchema

ORPHAN_BASE = 900_000_000


class DefectError(ShapeError, ValueError):
    """A defect names something the tables do not have, or has a bad value."""


def _rows(defect: Mapping[str, Any], n: int, rng: np.random.Generator) -> np.ndarray:
    fraction = float(defect.get("fraction", 0.05))
    if not 0 < fraction <= 1:
        raise DefectError(f"{defect['kind']}: 'fraction' must be above 0 and at most 1")
    count = min(n, max(1, round(n * fraction)))
    return np.sort(rng.choice(n, size=count, replace=False))


def _column(table: pa.Table, defect: Mapping[str, Any], name: str) -> int:
    if name not in table.column_names:
        raise DefectError(f"{defect['kind']}: the table has no column {name!r}")
    return int(table.column_names.index(name))


def _replace(table: pa.Table, index: int, values: pa.Array | pa.ChunkedArray) -> pa.Table:
    return table.set_column(index, table.schema.field(index), values)


def inject_nulls(
    table: pa.Table, defect: Mapping[str, Any], rng: np.random.Generator, schema: GenSchema
) -> tuple[pa.Table, int]:
    i = _column(table, defect, str(defect["column"]))
    rows = _rows(defect, table.num_rows, rng)
    mask = np.zeros(table.num_rows, dtype=bool)
    mask[rows] = True
    values = pc.if_else(pa.array(mask), pa.scalar(None, table.schema.field(i).type), table[i])
    out = table.set_column(i, table.schema.field(i).with_nullable(True), values)
    return out, int(len(rows))


def duplicate_keys(
    table: pa.Table, defect: Mapping[str, Any], rng: np.random.Generator, schema: GenSchema
) -> tuple[pa.Table, int]:
    name = defect.get("column")
    if name is None:
        key = schema.tables[str(defect["table"])].primary_key
        if len(key) != 1:
            raise DefectError(f"duplicate_keys: {defect['table']} needs a single-column key")
        name = key[0]
    i = _column(table, defect, str(name))
    if table.num_rows < 2:
        raise DefectError("duplicate_keys: the table needs at least two rows")
    rows = _rows(defect, table.num_rows, rng)
    keep = np.setdiff1d(np.arange(table.num_rows), rows)
    if len(keep) == 0:
        raise DefectError("duplicate_keys: 'fraction' leaves no row to copy a key from")
    source = rng.choice(keep, size=len(rows))
    order = np.arange(table.num_rows)
    order[rows] = source
    return _replace(table, i, table[i].take(pa.array(order))), int(len(rows))


def orphan_keys(
    table: pa.Table, defect: Mapping[str, Any], rng: np.random.Generator, schema: GenSchema
) -> tuple[pa.Table, int]:
    i = _column(table, defect, str(defect["column"]))
    typ = table.schema.field(i).type
    if not pa.types.is_integer(typ):
        raise DefectError(f"orphan_keys: {defect['table']}.{defect['column']} is not an integer")
    rows = _rows(defect, table.num_rows, rng)
    orphans = ORPHAN_BASE + np.arange(len(rows))
    values = np.asarray(table[i].fill_null(0).to_numpy(zero_copy_only=False))
    values = values.astype(np.int64)
    values[rows] = orphans
    valid = np.asarray(table[i].is_valid().to_numpy(zero_copy_only=False), dtype=bool)
    valid[rows] = True
    return _replace(table, i, pa.array(values, mask=~valid).cast(typ)), int(len(rows))


def late_arrivals(
    table: pa.Table, defect: Mapping[str, Any], rng: np.random.Generator, schema: GenSchema
) -> tuple[pa.Table, int]:
    i = _column(table, defect, str(defect["column"]))
    typ = table.schema.field(i).type
    if not (pa.types.is_timestamp(typ) or pa.types.is_date(typ)):
        raise DefectError(f"late_arrivals: {defect['table']}.{defect['column']} is not a date")
    days = int(defect.get("days", 30))
    if days < 1:
        raise DefectError("late_arrivals: 'days' must be at least 1")
    rows = _rows(defect, table.num_rows, rng)
    mask = np.zeros(table.num_rows, dtype=bool)
    mask[rows] = True
    shifted = pc.subtract(table[i], pa.scalar(dt.timedelta(days=days))).cast(typ)
    return _replace(table, i, pc.if_else(pa.array(mask), shifted, table[i])), int(len(rows))


DEFECTS: dict[str, Callable[..., tuple[pa.Table, int]]] = {
    "inject_nulls": inject_nulls,
    "duplicate_keys": duplicate_keys,
    "orphan_keys": orphan_keys,
    "late_arrivals": late_arrivals,
}


def check_defect(defect: Any, schema: GenSchema) -> None:
    """Raise :class:`DefectError` when ``defect`` is malformed or names no table or column of
    ``schema``."""
    if not isinstance(defect, Mapping) or defect.get("kind") not in DEFECTS:
        raise DefectError(f"a defect needs a 'kind' of {', '.join(DEFECTS)}: {defect!r}")
    table = schema.tables.get(str(defect.get("table")))
    if table is None:
        raise DefectError(f"{defect['kind']}: the schema has no table {defect.get('table')!r}")
    column = defect.get("column")
    if column is None and defect["kind"] != "duplicate_keys":
        raise DefectError(f"{defect['kind']} needs a 'column'")
    if column is not None and column not in table.columns:
        raise DefectError(f"{defect['kind']}: no column {defect['table']}.{column}")


def apply_defects(
    tables: Mapping[str, pa.Table],
    defects: list[Mapping[str, Any]],
    schema: GenSchema,
    seed: int,
) -> tuple[dict[str, pa.Table], dict[str, int]]:
    """The tables with every defect planted, and the rows changed per defect kind."""
    out = dict(tables)
    changed: dict[str, int] = {}
    for position, defect in enumerate(defects):
        check_defect(defect, schema)
        name = str(defect["table"])
        rng = np.random.default_rng([seed, position])
        out[name], rows = DEFECTS[str(defect["kind"])](out[name], defect, rng, schema)
        changed[str(defect["kind"])] = changed.get(str(defect["kind"]), 0) + rows
    return out, changed
