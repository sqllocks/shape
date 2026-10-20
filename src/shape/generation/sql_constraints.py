"""Constraint options derived from generation declarations, never arbitrary SQL.

Expressions are data: comparison nodes contain ``op``, ``left`` and ``right``;
operands are ``{"column": name}`` or ``{"literal": value}``. ``and`` nodes
contain ``args``; ``in`` nodes contain ``left`` and scalar ``values``. Sinks
render this small vocabulary with their identifier and literal quoting functions.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from shape.generation.rules import MAX_RULE_CHARS, parse_comparison
from shape.generation.schema import GenSchema
from shape.security.sqltext import check_identifier

_NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_BETWEEN = re.compile(rf"^(\w+)\s+BETWEEN\s+({_NUMBER})\s+AND\s+({_NUMBER})$", re.I)
_COMPARISONS = frozenset({"=", ">=", "<=", ">", "<"})


def _name(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("a constraint identifier must be a non-empty string")
    return check_identifier(value)


def _literal(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, Decimal) and value.is_finite():
        return value
    raise ValueError("a constraint literal must be a finite scalar, never SQL text or an object")


def render_expression(
    expression: Any,
    *,
    quote: Callable[[str], str],
    literal: Callable[[Any], str],
    columns: Sequence[str],
) -> str:
    """Validate and render the constrained expression vocabulary using a sink's quoting.

    Unknown columns, extra node members, arbitrary SQL, nonfinite or compound
    literals, trees deeper than 32 nodes and trees over 1,024 nodes are refused.
    """
    known = set(columns)
    remaining = 1024

    def mapping(node: Any, keys: set[str]) -> Mapping[str, Any]:
        if not isinstance(node, Mapping) or set(node) != keys:
            raise ValueError("invalid constraint expression node")
        return node

    def operand(node: Any) -> str:
        if isinstance(node, Mapping) and set(node) == {"column"}:
            name = _name(node["column"])
            if name not in known:
                raise ValueError(f"constraint names unknown column {name!r}")
            return quote(name)
        node = mapping(node, {"literal"})
        return literal(_literal(node["literal"]))

    def visit(node: Any, depth: int) -> str:
        nonlocal remaining
        remaining -= 1
        if depth > 32:
            raise ValueError("constraint expression is too deep")
        if remaining < 0:
            raise ValueError("constraint expression has too many nodes")
        if not isinstance(node, Mapping):
            raise ValueError("a constraint expression must be structured data, never SQL text")
        op = node.get("op")
        if isinstance(op, str) and op in _COMPARISONS:
            node = mapping(node, {"op", "left", "right"})
            return f"({operand(node['left'])} {op} {operand(node['right'])})"
        if op == "and":
            node = mapping(node, {"op", "args"})
            args = node["args"]
            if not isinstance(args, list) or not 1 <= len(args) <= 1024:
                raise ValueError("constraint conjunction requires 1 to 1024 expressions")
            return "(" + " AND ".join(visit(arg, depth + 1) for arg in args) + ")"
        if op == "in":
            node = mapping(node, {"op", "left", "values"})
            values = node["values"]
            if not isinstance(values, list) or not 1 <= len(values) <= 1024:
                raise ValueError("constraint enum requires 1 to 1024 literals")
            rendered = ", ".join(literal(_literal(value)) for value in values)
            return f"({operand(node['left'])} IN ({rendered}))"
        if op == "is_null":
            node = mapping(node, {"op", "left"})
            return f"({operand(node['left'])} IS NULL)"
        raise ValueError("unknown constraint expression operator")

    return visit(expression, 0)


def _comparison(column: str, op: str, right: Any) -> dict[str, Any]:
    return {"op": op, "left": {"column": column}, "right": right}


def _number(text: str) -> int | Decimal:
    if re.fullmatch(_NUMBER, text) is None:
        raise ValueError("expected a numeric literal or a declared column")
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise ValueError("invalid numeric literal") from None
    _literal(value)
    if abs(value.adjusted()) > 1000:
        raise ValueError("numeric literal exponent is too large")
    return int(value) if value == value.to_integral_value() else value


def _rule_expression(text: str, columns: Sequence[str]) -> dict[str, Any]:
    if len(text) > MAX_RULE_CHARS:
        raise ValueError("constraint rule is too long")
    match = _BETWEEN.fullmatch(text.strip())
    if match:
        name, low_text, high_text = match.groups()
        low, high = _number(low_text), _number(high_text)
        if name not in columns or low > high:
            raise ValueError("invalid BETWEEN column or reversed bounds")
        return {
            "op": "and",
            "args": [
                _comparison(name, ">=", {"literal": low}),
                _comparison(name, "<=", {"literal": high}),
            ],
        }
    left, op, right = parse_comparison(text)
    if not op or left not in columns:
        raise ValueError("expected a comparison over declared columns")
    operand = {"column": right} if right in columns else {"literal": _number(right)}
    return _comparison(left, "=" if op == "==" else op, operand)


def derive_constraints(
    schema: GenSchema,
    table: str,
    *,
    constraints: str = "keys",
    contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Derive foreign/unique keys, safe checks and literal defaults for one table.

    ``none`` adds nothing to the historical primary-key options. ``keys`` is
    the default; ``all`` also includes checks/defaults. ``disable`` derives keys
    while retaining the SQL Server sink's separate constraint-toggle behavior.
    """
    if constraints not in ("keys", "all", "none", "disable"):
        raise ValueError("constraints must be keys, all, none or disable")
    if constraints == "none":
        return {}
    tdef = schema.tables[table]
    foreign: list[dict[str, Any]] = []
    pairs: set[tuple[tuple[str, ...], str, tuple[str, ...]]] = set()
    unique: list[list[str]] = []
    checks: list[dict[str, Any]] = []
    defaults: dict[str, Any] = {}

    def reference(name: str, child: list[str], parent: str, target: list[str]) -> None:
        _name(name)
        if (
            not child
            or len(child) != len(target)
            or parent not in schema.tables
            or any(c not in tdef.columns for c in child)
            or any(c not in schema.tables[parent].columns for c in target)
        ):
            raise ValueError(f"invalid foreign key {name!r}")
        for identifier in [*child, parent, *target]:
            _name(identifier)
        key = (tuple(child), parent, tuple(target))
        if key not in pairs:
            foreign.append(
                {
                    "columns": list(child),
                    "ref_table": parent,
                    "ref_columns": list(target),
                    "name": name,
                }
            )
            pairs.add(key)

    for rel in schema.relationships:
        if rel.child == table:
            reference(rel.name, rel.child_columns, rel.parent, rel.parent_columns)
            if rel.type == "one_to_one" and rel.child_columns not in unique:
                unique.append(list(rel.child_columns))
    for col in tdef.columns.values():
        if col.is_foreign_key:
            reference(
                f"fk_{table}_{col.name}",
                [col.name],
                col.fk_ref_table or "",
                [col.fk_ref_column or ""],
            )
        if col.strategy == "composite_foreign_key":
            target = col.generator.get("ref_columns")
            if not isinstance(target, list) or not target:
                raise ValueError(f"invalid composite foreign key {col.name!r}")
            child = [col.name]
            for parent_column in target[1:]:
                matches = [
                    other.name
                    for other in tdef.columns.values()
                    if other.strategy == "composite_fk_field"
                    and other.generator.get("source_column") == col.name
                    and other.generator.get("ref_column") == parent_column
                ]
                if len(matches) != 1:
                    raise ValueError(f"incomplete composite foreign key {col.name!r}")
                child.extend(matches)
            reference(f"fk_{table}_{col.name}", child, col.generator.get("ref_table", ""), target)
        if constraints == "all" and col.strategy == "constant":
            defaults[col.name] = _literal(col.generator.get("value"))
    if contract is not None:
        from shape import compat

        compat.check_readable("contract", contract)
        subtables = contract.get("tables")
        sub = subtables.get(table, {}) if isinstance(subtables, Mapping) else contract
        if not isinstance(sub, Mapping) or not isinstance(sub.get("columns", {}), Mapping):
            raise ValueError("contract columns must be an object")
        for name, rules in sub.get("columns", {}).items():
            if name not in tdef.columns or not isinstance(rules, Mapping):
                raise ValueError(f"invalid contract column {name!r} for table {table!r}")
            if rules.get("unique") is True and [name] not in unique:
                unique.append([name])
            if constraints != "all":
                continue
            for bound, op in (("min", ">="), ("max", "<=")):
                if bound in rules:
                    value = _literal(rules[bound])
                    if isinstance(value, (str, bool)) or value is None:
                        raise ValueError(f"contract {name}.{bound} must be numeric")
                    checks.append(
                        {
                            "name": f"ck_{table}_{name}_{bound}",
                            "expression": _comparison(name, op, {"literal": value}),
                        }
                    )
            values = rules.get("accepted_values", rules.get("allowed_values"))
            if values is not None:
                if not isinstance(values, list) or not values:
                    raise ValueError(f"contract {name}.accepted_values must be a non-empty list")
                values = [_literal(v) for v in values if v is not None]
                expression = (
                    {"op": "in", "left": {"column": name}, "values": values}
                    if values
                    else {"op": "is_null", "left": {"column": name}}
                )
                checks.append({"name": f"ck_{table}_{name}_enum", "expression": expression})
            if "default" in rules:
                defaults[name] = _literal(rules["default"])
    if constraints == "all":
        for rule in schema.business_rules:
            if rule.table != table or rule.type not in ("constraint", "cross_column"):
                continue
            try:
                _name(rule.name)
                if rule.when:
                    raise ValueError("conditional rules cannot be emitted as unconditional CHECKs")
                expression = _rule_expression(rule.rule, tdef.column_names)
            except ValueError as exc:
                raise ValueError(f"constraint {rule.name!r}: {exc}") from None
            checks.append({"name": rule.name, "expression": expression})
    names = [check["name"] for check in checks]
    if len(set(names)) != len(names):
        raise ValueError("duplicate check constraint name")
    for check in checks:
        _name(check["name"])
        render_expression(
            check["expression"], quote=lambda n: n, literal=str, columns=tdef.column_names
        )
    return {"foreign_keys": foreign, "unique": unique, "checks": checks, "defaults": defaults}
