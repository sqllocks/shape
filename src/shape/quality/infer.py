"""Infer conservative quality rules from a captured Shape."""

from __future__ import annotations

from typing import Any

from shape.spec.view import columns_of, distinct_bounds, model_of, table_of

from .policy import Rule


def infer_rules(shape: Any, strict: bool = False, table: str | None = None) -> tuple[Rule, ...]:
    """Rules a column's evidence supports: ``not_null`` when it has no nulls, ``min``/``max``
    for numeric columns, and ``unique`` when the number of non-null rows lies within the
    column's distinct count (equal to it, if exact; within the sketch's error bound, if not).
    ``shape`` is a v2 model or a v1 capture (migrated)."""
    t = table_of(model_of(shape), table)
    rows = int(t["rows"])
    rules = []
    for name, c in columns_of(t).items():
        if (c.get("null_count") or 0) == 0:
            rules.append(Rule(name, "not_null", severity="error" if strict else "warning"))
        if c.get("kind") in ("int", "float"):
            if c.get("min") is not None:
                rules.append(Rule(name, "min", c["min"], "warning"))
            if c.get("max") is not None:
                rules.append(Rule(name, "max", c["max"], "warning"))
        non_null = rows - (c.get("null_count") or 0)
        low, high = distinct_bounds(c)
        if non_null > 0 and low <= non_null <= high:
            rules.append(Rule(name, "unique", severity="warning"))
    return tuple(rules)
