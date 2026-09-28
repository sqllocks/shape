"""Infer conservative quality rules from a captured Shape."""

from __future__ import annotations

from .policy import Rule


def infer_rules(shape, strict=False):
    rules = []
    rows = max(int(shape.get("rows", 0)), 1)
    for name, c in shape.get("columns", {}).items():
        if c.get("null_count", 0) == 0:
            rules.append(Rule(name, "not_null", severity="error" if strict else "warning"))
        if c.get("kind") == "numeric":
            if c.get("min") is not None:
                rules.append(Rule(name, "min", c["min"], "warning"))
            if c.get("max") is not None:
                rules.append(Rule(name, "max", c["max"], "warning"))
        if c.get("distinct_estimate", 0) >= rows * 0.999:
            rules.append(Rule(name, "unique", severity="warning"))
    return tuple(rules)
