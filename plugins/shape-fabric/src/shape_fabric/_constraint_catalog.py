"""Read existing SQL constraint declarations before any row-changing statement."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from shape.errors import ShapeError
from shape.generation.sql_constraints import render_expression
from shape.generation.write_constraints import ConstraintDDL, literal

from . import _tsql

_TOKEN = re.compile(
    r"N?'(?:[^']|'')*'|\[(?:[^\]]|\]\])*\]|\"(?:[^\"]|\"\")*\"|>=|<=|<>|!="
    r"|[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?|[A-Za-z_][A-Za-z_0-9]*|[()=><,+*/%-]"
)


def expression_tokens(sql: str) -> tuple[str, ...]:
    """Ignore spacing and whole/atomic grouping, preserving meaningful inner grouping.

    This is only a conservative equality comparison with a generated, already validated
    expression. No catalog text is executed; an unfamiliar spelling refuses the load.
    """
    tokens: list[str] = []
    offset = 0
    for match in _TOKEN.finditer(sql):
        if sql[offset : match.start()].strip():
            return ("unrecognized catalog expression", sql)
        tokens.append(match[0])
        offset = match.end()
    if sql[offset:].strip():
        return ("unrecognized catalog expression", sql)
    depth = 0
    for token in tokens:
        depth += (token == "(") - (token == ")")
        if depth < 0:
            return ("unrecognized catalog expression", sql)
    if depth:
        return ("unrecognized catalog expression", sql)
    # SQL Server wraps DEFAULT literals and CHECK operands in redundant parentheses.
    # Remove only a single atomic token or a group enclosing the entire expression.
    # Flattening arbitrary inner groups could accept a different Boolean/arithmetic rule.
    changed = True
    while changed:
        changed = False
        for index in range(len(tokens) - 2):
            if (
                tokens[index] == "("
                and tokens[index + 2] == ")"
                and tokens[index + 1]
                not in {
                    "(",
                    ")",
                    "AND",
                    "OR",
                    "NOT",
                    "IN",
                    "IS",
                    ",",
                    "=",
                    ">",
                    "<",
                    ">=",
                    "<=",
                    "<>",
                    "!=",
                    "+",
                    "-",
                    "*",
                    "/",
                    "%",
                }
            ):
                tokens[index : index + 3] = [tokens[index + 1]]
                changed = True
                break
        if tokens and tokens[0] == "(" and tokens[-1] == ")":
            depth = 0
            for _index, token in enumerate(tokens):
                depth += (token == "(") - (token == ")")
                if depth == 0:
                    break
            if _index == len(tokens) - 1:
                tokens = tokens[1:-1]
                changed = True
    return tuple(tokens)


def verify_constraints(
    db: Any,
    schema_name: str,
    table: str,
    options: Mapping[str, Any],
    compiled: ConstraintDDL,
) -> None:
    qualified = _tsql.qualified(schema_name, table)
    warehouse = db.warehouse
    if options.get("primary_key"):
        rows = db.execute(
            "SELECT kc.name, col.name, ic.key_ordinal FROM sys.key_constraints kc "
            "JOIN sys.index_columns ic ON ic.object_id = kc.parent_object_id "
            "AND ic.index_id = kc.unique_index_id "
            "JOIN sys.columns col ON col.object_id = ic.object_id AND col.column_id = ic.column_id "
            "WHERE kc.parent_object_id = OBJECT_ID(?) AND kc.type = 'PK' "
            "ORDER BY kc.name, ic.key_ordinal",
            qualified,
        ).fetchall()
        primary_keys: dict[str, list[str]] = defaultdict(list)
        for name, column, _ in rows:
            primary_keys[name].append(column)
        if list(options["primary_key"]) not in primary_keys.values():
            raise ShapeError(f"existing table {table!r}: PRIMARY KEY differs")
    if options.get("unique"):
        rows = db.execute(
            "SELECT kc.name, col.name, ic.key_ordinal FROM sys.key_constraints kc "
            "JOIN sys.index_columns ic ON ic.object_id = kc.parent_object_id "
            "AND ic.index_id = kc.unique_index_id "
            "JOIN sys.columns col ON col.object_id = ic.object_id AND col.column_id = ic.column_id "
            "WHERE kc.parent_object_id = OBJECT_ID(?) AND kc.type = 'UQ' "
            "ORDER BY kc.name, ic.key_ordinal",
            qualified,
        ).fetchall()
        keys: dict[str, list[str]] = defaultdict(list)
        for name, column, _ in rows:
            keys[name].append(column)
        for columns in options["unique"]:
            if columns not in keys.values():
                raise ShapeError(f"existing table {table!r}: UNIQUE {columns!r} is missing")
    if options.get("foreign_keys"):
        rows = db.execute(
            "SELECT fk.name, cc.name, OBJECT_SCHEMA_NAME(fk.referenced_object_id), "
            "OBJECT_NAME(fk.referenced_object_id), pc.name, fk.is_disabled, fk.is_not_trusted "
            "FROM sys.foreign_keys fk JOIN sys.foreign_key_columns fkc "
            "ON fkc.constraint_object_id = fk.object_id "
            "JOIN sys.columns cc ON cc.object_id = fk.parent_object_id "
            "AND cc.column_id = fkc.parent_column_id "
            "JOIN sys.columns pc ON pc.object_id = fk.referenced_object_id "
            "AND pc.column_id = fkc.referenced_column_id "
            "WHERE fk.parent_object_id = OBJECT_ID(?) ORDER BY fk.name, fkc.constraint_column_id",
            qualified,
        ).fetchall()
        refs: dict[str, tuple[list[str], str, str, list[str]]] = {}
        for name, column, parent_schema, parent, target, disabled, untrusted in rows:
            if (disabled or untrusted) and not warehouse:
                continue
            reference_parts = refs.setdefault(name, ([], parent_schema, parent, []))
            reference_parts[0].append(column)
            reference_parts[3].append(target)
        for ref in options["foreign_keys"]:
            expected = (ref["columns"], schema_name, ref["ref_table"], ref["ref_columns"])
            if expected not in refs.values():
                raise ShapeError(f"existing table {table!r}: foreign key {ref['name']!r} differs")
    if options.get("checks") and not warehouse:
        rows = db.execute(
            "SELECT name, definition, is_disabled, is_not_trusted FROM sys.check_constraints "
            "WHERE parent_object_id = OBJECT_ID(?)",
            qualified,
        ).fetchall()
        checks = {
            expression_tokens(definition)
            for _, definition, disabled, untrusted in rows
            if not disabled and not untrusted
        }
        for check in options["checks"]:
            expression = render_expression(
                check["expression"],
                quote=_tsql.ident,
                literal=lambda value: literal(value, "tsql"),
                columns=options["schema"].names,
            )
            if expression_tokens(expression) not in checks:
                raise ShapeError(f"existing table {table!r}: CHECK {check['name']!r} differs")
    if compiled.defaults:
        rows = db.execute(
            "SELECT col.name, def.definition FROM sys.default_constraints def "
            "JOIN sys.columns col ON col.object_id = def.parent_object_id "
            "AND col.column_id = def.parent_column_id "
            "WHERE def.parent_object_id = OBJECT_ID(?)",
            qualified,
        ).fetchall()
        defaults = {column: expression_tokens(definition) for column, definition in rows}
        for column, value in compiled.defaults.items():
            if defaults.get(column) != expression_tokens(value):
                raise ShapeError(f"existing table {table!r}: DEFAULT for {column!r} differs")
