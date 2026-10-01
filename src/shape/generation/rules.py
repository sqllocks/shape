"""Business rules: find the rows that break a rule, and repair them.

A rule is ``"left OP right"`` with ``OP`` one of ``>=``, ``<=``, ``>``, ``<``, ``==`` and a
``type``:

* ``cross_column`` and ``constraint``: ``left`` is a column of ``table``; ``right`` is another
  column of it or a number.
* ``cross_table``: ``left`` and ``right`` are ``table.column`` of two tables joined on the ``via``
  column both have (a left join from the ``left`` table).

Other rule types are carried in the schema and ignored here. :func:`validate_rules` returns one
:class:`RuleViolation` per broken rule. :func:`fix_rules` repairs ``cross_column`` rules (``<``
and ``>``) and ``cross_table`` rules (``>=``, ``>`` and ``<=``) and returns the repaired tables
and whatever still violates. Fixes draw from row-addressed streams (``rng.RowStream``, label
``fix:<rule name>``), so a repair does not depend on how the tables were chunked or in what order
rules ran in a previous process.

Tables are Arrow tables; a repaired column keeps its Arrow type (an integer column stays
integer).

Stable interface: ``RuleViolation``, ``validate_rules`` and ``fix_rules``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.generation.rng import RowStream
from shape.generation.schema import BusinessRule, GenSchema

_COMPARISON = re.compile(r"^(.+?)\s*(>=|<=|>|<|==)\s*(.+)$")

Tables = dict[str, pa.Table]


@dataclass(frozen=True, slots=True)
class RuleViolation:
    """A rule that ``violation_count`` of ``total_rows`` rows of ``table`` break."""

    rule_name: str
    table: str
    violation_count: int
    total_rows: int

    @property
    def violation_rate(self) -> float:
        return self.violation_count / self.total_rows if self.total_rows > 0 else 0.0

    def __repr__(self) -> str:
        return (
            f"RuleViolation('{self.rule_name}' on {self.table}: "
            f"{self.violation_count}/{self.total_rows} = {self.violation_rate:.1%})"
        )


def parse_comparison(rule: str) -> tuple[str, str, str]:
    """``"A >= B"`` as ``("A", ">=", "B")``; three empty strings when it is not a comparison."""
    m = _COMPARISON.match(rule.strip())
    return (m.group(1).strip(), m.group(2), m.group(3).strip()) if m else ("", "", "")


# ---- column values as numpy ---------------------------------------------------------------


def _is_temporal(arr: pa.ChunkedArray | pa.Array) -> bool:
    return bool(pa.types.is_timestamp(arr.type) or pa.types.is_date(arr.type))


def _numpy(arr: pa.ChunkedArray | pa.Array) -> npt.NDArray[Any]:
    """Floats (NaN for null) for numeric columns, ``datetime64[us]`` (NaT for null) for temporal
    ones."""
    if _is_temporal(arr):
        return np.asarray(pc.cast(arr, pa.timestamp("us")).to_numpy(zero_copy_only=False))
    return np.asarray(pc.cast(arr, pa.float64()).to_numpy(zero_copy_only=False), dtype=np.float64)


def _evaluable(arr: pa.ChunkedArray | pa.Array) -> bool:
    t = arr.type
    return bool(
        _is_temporal(arr)
        or pa.types.is_integer(t)
        or pa.types.is_floating(t)
        or pa.types.is_decimal(t)
        or pa.types.is_boolean(t)
    )


def _count_violations(left: npt.NDArray[Any], op: str, right: npt.NDArray[Any]) -> int | None:
    """Rows where ``left op right`` is false, a missing value never counting as a violation of an
    ordering (``==`` counts it, as ``!=`` does for floats)."""
    if op == ">":
        bad = left <= right
    elif op == ">=":
        bad = left < right
    elif op == "<":
        bad = left >= right
    elif op == "<=":
        bad = left > right
    elif op == "==":
        bad = left != right
    else:
        return None
    return int(np.count_nonzero(bad))


# ---- validate -----------------------------------------------------------------------------


def _check_single_table(rule: BusinessRule, tables: Tables) -> RuleViolation | None:
    if not rule.table or rule.table not in tables:
        return None
    table = tables[rule.table]
    left, op, right = parse_comparison(rule.rule)
    if not left or left not in table.column_names:
        return None
    lcol = table[left]
    if not _evaluable(lcol):
        return None
    if right in table.column_names:
        rcol = table[right]
        if not _evaluable(rcol) or _is_temporal(lcol) != _is_temporal(rcol):
            return None
        count = _count_violations(_numpy(lcol), op, _numpy(rcol))
    else:
        try:
            literal = float(right)
        except ValueError:
            return None
        if _is_temporal(lcol):
            return None
        count = _count_violations(_numpy(lcol), op, np.float64(literal))  # type: ignore[arg-type]
    if not count:
        return None
    return RuleViolation(rule.name, rule.table, count, table.num_rows)


def _cross_table_parts(
    rule: BusinessRule, tables: Tables
) -> tuple[str, str, str, str, str, pa.Table, pa.Table] | None:
    if not rule.via:
        return None
    left, op, right = parse_comparison(rule.rule)
    if not left or not op or not right or "." not in left or "." not in right:
        return None
    ltable, lcol = left.split(".", 1)
    rtable, rcol = right.split(".", 1)
    if ltable not in tables or rtable not in tables:
        return None
    lt, rt = tables[ltable], tables[rtable]
    if lcol not in lt.column_names or rcol not in rt.column_names:
        return None
    if rule.via not in lt.column_names or rule.via not in rt.column_names:
        return None
    return ltable, lcol, op, rtable, rcol, lt, rt


def _check_cross_table(rule: BusinessRule, tables: Tables) -> RuleViolation | None:
    parts = _cross_table_parts(rule, tables)
    if parts is None:
        return None
    ltable, lcol, op, _rtable, rcol, lt, rt = parts
    assert rule.via is not None
    if not (_evaluable(lt[lcol]) and _evaluable(rt[rcol])):
        return None
    if _is_temporal(lt[lcol]) != _is_temporal(rt[rcol]):
        return None
    merged = pa.table({"v": lt[rule.via], "l": lt[lcol]}).join(
        pa.table({"v": rt[rule.via], "r": rt[rcol]}), keys="v", join_type="left outer"
    )
    count = _count_violations(_numpy(merged["l"]), op, _numpy(merged["r"]))
    if not count:
        return None
    return RuleViolation(rule.name, ltable, count, merged.num_rows)


def validate_rules(tables: Tables, schema: GenSchema) -> list[RuleViolation]:
    """Every business rule of ``schema`` that ``tables`` break."""
    out: list[RuleViolation] = []
    for rule in schema.business_rules:
        v: RuleViolation | None = None
        if rule.type in ("cross_column", "constraint"):
            v = _check_single_table(rule, tables)
        elif rule.type == "cross_table":
            v = _check_cross_table(rule, tables)
        if v is not None:
            out.append(v)
    return out


# ---- fix ----------------------------------------------------------------------------------


def _replace(
    table: pa.Table, column: str, mask: npt.NDArray[np.bool_], values: npt.NDArray[Any]
) -> pa.Table:
    """``table`` with ``column`` set to ``values`` where ``mask``, keeping the column's type."""
    old = table[column]
    new = pa.array(values, from_pandas=True)
    if pa.types.is_integer(old.type):
        new = pc.cast(pc.round(pc.cast(new, pa.float64())), old.type)
    else:
        new = pc.cast(new, old.type)
    fixed = pc.if_else(pa.array(mask), new, old.combine_chunks() if old.num_chunks else old)
    return table.set_column(table.column_names.index(column), column, fixed)


def _fix_cross_column(rule: BusinessRule, tables: Tables, seed: int) -> Tables:
    if not rule.table or rule.table not in tables:
        return tables
    table = tables[rule.table]
    left, op, right = parse_comparison(rule.rule)
    if not left or not right or left not in table.column_names or right not in table.column_names:
        return tables
    lcol, rcol = table[left], table[right]
    if not (_evaluable(lcol) and _evaluable(rcol)) or _is_temporal(lcol) != _is_temporal(rcol):
        return tables
    n = table.num_rows
    lv, rv = _numpy(lcol), _numpy(rcol)
    stream = RowStream(seed, rule.table, left, f"fix:{rule.name}")
    u = stream.uniform(0, n)
    if _is_temporal(lcol):
        days = (1 + np.floor(u * 29)).astype("timedelta64[D]").astype("timedelta64[us]")
        if op == "<":
            mask, new = lv >= rv, rv - days
        elif op == ">":
            mask, new = lv <= rv, rv + days
        else:
            return tables
    else:
        if op == "<":
            mask, new = lv >= rv, np.round(rv * (0.3 + u * 0.65), 2)
        elif op == ">":
            mask, new = lv <= rv, np.round(rv * (1.05 + u * 0.95), 2)
        else:
            return tables
    if not mask.any():
        return tables
    return {**tables, rule.table: _replace(table, left, mask, np.where(mask, new, lv))}


def _fix_cross_table(rule: BusinessRule, tables: Tables, seed: int) -> Tables:
    parts = _cross_table_parts(rule, tables)
    if parts is None:
        return tables
    ltable, lcol, op, _rtable, rcol, lt, rt = parts
    assert rule.via is not None
    left_col, right_col = lt[lcol], rt[rcol]
    if not (_evaluable(left_col) and _evaluable(right_col)):
        return tables
    if _is_temporal(left_col) != _is_temporal(right_col):
        return tables
    pos = pc.index_in(lt[rule.via], value_set=rt[rule.via].combine_chunks())
    right_vals = _numpy(pc.take(right_col, pos))  # NaN / NaT where no parent row matched
    lv = _numpy(left_col)
    temporal = _is_temporal(left_col)
    offset = np.timedelta64(1, "D") if temporal else 1.0
    if op in (">=", ">"):
        mask = (lv < right_vals) if op == ">=" else (lv <= right_vals)
        new = right_vals + offset
    elif op == "<=":
        mask = lv > right_vals
        u = RowStream(seed, ltable, lcol, f"fix:{rule.name}").uniform(0, lt.num_rows)
        new = np.round(right_vals * (0.3 + u * 0.7), 2)
    else:
        return tables
    if not mask.any():
        return tables
    return {**tables, ltable: _replace(lt, lcol, mask, np.where(mask, new, lv))}


def fix_rules(tables: Tables, schema: GenSchema) -> tuple[Tables, list[RuleViolation]]:
    """Repair ``tables`` for every ``cross_table`` and ``cross_column`` rule, in schema order;
    return the repaired tables (the inputs are not changed) and the violations that remain."""
    out = dict(tables)
    seed = schema.model.seed
    for rule in schema.business_rules:
        if rule.type == "cross_table":
            out = _fix_cross_table(rule, out, seed)
        elif rule.type == "cross_column":
            out = _fix_cross_column(rule, out, seed)
    return out, validate_rules(out, schema)
