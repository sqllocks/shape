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

from shape.generation.arrowkit import array as arrow_array
from shape.generation.arrowkit import to_numpy as arrow_numpy
from shape.generation.keypos import dense_start, first_rows
from shape.generation.rng import RowStream
from shape.generation.schema import BusinessRule, GenSchema

MAX_RULE_CHARS = 2000
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
    text = rule.strip()
    if len(text) > MAX_RULE_CHARS:  # the pattern below is quadratic on a long run of spaces
        return "", "", ""
    m = _COMPARISON.match(text)
    return (m.group(1).strip(), m.group(2), m.group(3).strip()) if m else ("", "", "")


# ---- column values as numpy ---------------------------------------------------------------


def _is_temporal(arr: pa.ChunkedArray | pa.Array) -> bool:
    return bool(pa.types.is_timestamp(arr.type) or pa.types.is_date(arr.type))


def _numpy(arr: pa.ChunkedArray | pa.Array) -> npt.NDArray[Any]:
    """Floats (NaN for null) for numeric columns, ``datetime64[us]`` (NaT for null) for temporal
    ones."""
    if _is_temporal(arr):
        return np.asarray(arrow_numpy(pc.cast(arr, pa.timestamp("us"))))
    return np.asarray(arrow_numpy(pc.cast(arr, pa.float64())), dtype=np.float64)


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


def _take_first(values: pa.ChunkedArray, probe: pa.ChunkedArray, keys: pa.ChunkedArray) -> pa.Array:
    """``values`` at the first row of ``keys`` equal to each ``probe`` value; null where none."""
    return pc.take(values, first_rows(probe, keys))


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
    if dense_start(rt[rule.via]) is not None:  # unique keys: the join is a row lookup
        count = _count_violations(
            _numpy(lt[lcol]), op, _numpy(_take_first(rt[rcol], lt[rule.via], rt[rule.via]))
        )
        rows = lt.num_rows
    else:
        merged = pa.table({"v": lt[rule.via], "l": lt[lcol]}).join(
            pa.table({"v": rt[rule.via], "r": rt[rcol]}), keys="v", join_type="left outer"
        )
        count = _count_violations(_numpy(merged["l"]), op, _numpy(merged["r"]))
        rows = merged.num_rows
    if not count:
        return None
    return RuleViolation(rule.name, ltable, count, rows)


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
    new = arrow_array(values, from_pandas=True)
    if pa.types.is_integer(old.type):
        new = pc.cast(pc.round(pc.cast(new, pa.float64())), old.type)
    else:
        new = pc.cast(new, old.type)
    fixed = pc.if_else(arrow_array(mask), new, old.combine_chunks() if old.num_chunks else old)
    return table.set_column(table.column_names.index(column), column, fixed)


def _fix_cross_column(rule: BusinessRule, tables: Tables, seed: int, row_start: int) -> Tables:
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
    u = stream.uniform(row_start, n)
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


def _fix_cross_table(rule: BusinessRule, tables: Tables, seed: int, row_start: int) -> Tables:
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
    right_vals = _numpy(_take_first(right_col, lt[rule.via], rt[rule.via]))  # NaN / NaT: no parent
    lv = _numpy(left_col)
    temporal = _is_temporal(left_col)
    offset = np.timedelta64(1, "D") if temporal else 1.0
    if op in (">=", ">"):
        mask = (lv < right_vals) if op == ">=" else (lv <= right_vals)
        new = right_vals + offset
    elif op == "<=":
        mask = lv > right_vals
        u = RowStream(seed, ltable, lcol, f"fix:{rule.name}").uniform(row_start, lt.num_rows)
        new = np.round(right_vals * (0.3 + u * 0.7), 2)
    else:
        return tables
    if not mask.any():
        return tables
    return {**tables, ltable: _replace(lt, lcol, mask, np.where(mask, new, lv))}


# The comparisons :func:`fix_rule` repairs, by rule type; a rule with another operator, such as
# ``high >= low`` on one table, is validated but never changes a table.
_REPAIRED_OPS = {"cross_column": ("<", ">"), "cross_table": (">=", ">", "<=")}


def can_repair(rule: BusinessRule) -> bool:
    """Whether :func:`fix_rule` can change a table for ``rule`` (its type and operator are ones it
    repairs); a rule it cannot repair is only ever validated."""
    return parse_comparison(rule.rule)[1] in _REPAIRED_OPS.get(rule.type, ())


def repair_target(rule: BusinessRule) -> str | None:
    """The table :func:`fix_rule` can change for ``rule``: the table a ``cross_column`` rule names,
    the table on the left of a ``cross_table`` rule, none for any other rule (or for a comparison
    it does not repair)."""
    if not can_repair(rule):
        return None
    if rule.type == "cross_column":
        return rule.table or None
    left = parse_comparison(rule.rule)[0]
    return left.split(".", 1)[0] if "." in left else None


def repaired_tables(schema: GenSchema) -> set[str]:
    """The tables :func:`fix_rules` can change."""
    return {t for t in map(repair_target, schema.business_rules) if t}


def fix_rule(rule: BusinessRule, tables: Tables, seed: int, row_start: int = 0) -> Tables:
    """``tables`` with the violations of one rule repaired (the inputs are not changed).

    A repair looks at one row of the table it rewrites and, for ``cross_table``, at the row of the
    other table its ``via`` key names, and draws from a row-addressed stream. So the rewritten
    table may be a part of the whole table, rows ``row_start`` onwards of it, and the repaired
    rows are the rows the whole table has (the other table of a ``cross_table`` rule is always
    the whole one)."""
    if rule.type == "cross_table":
        return _fix_cross_table(rule, tables, seed, row_start)
    if rule.type == "cross_column":
        return _fix_cross_column(rule, tables, seed, row_start)
    return tables


def fix_rules(tables: Tables, schema: GenSchema) -> tuple[Tables, list[RuleViolation]]:
    """Repair ``tables`` for every ``cross_table`` and ``cross_column`` rule, in schema order;
    return the repaired tables (the inputs are not changed) and the violations that remain."""
    out = dict(tables)
    seed = schema.model.seed
    for rule in schema.business_rules:
        out = fix_rule(rule, out, seed)
    return out, validate_rules(out, schema)
