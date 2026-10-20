"""Cloud constraint metadata checks; catalog text is parsed, never executed."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from shape.errors import ShapeError
from shape.generation.sql_constraints import render_expression
from shape.generation.write_constraints import literal

from . import _sql
from ._constraint_catalog import _expression as _outer_expression
from ._constraint_catalog import _tokens

if TYPE_CHECKING:
    from ._base import Plan

INCOMING_DATABRICKS = (
    "SELECT fk.constraint_name AS inbound_constraint_name "
    "FROM system.information_schema.referential_constraints r "
    "JOIN system.information_schema.key_column_usage pk "
    "ON r.unique_constraint_catalog=pk.constraint_catalog "
    "AND r.unique_constraint_schema=pk.constraint_schema "
    "AND r.unique_constraint_name=pk.constraint_name "
    "JOIN system.information_schema.key_column_usage fk "
    "ON r.constraint_catalog=fk.constraint_catalog "
    "AND r.constraint_schema=fk.constraint_schema AND r.constraint_name=fk.constraint_name "
    "WHERE pk.table_catalog=current_catalog() "
    "AND pk.table_schema=COALESCE(?,current_schema()) AND pk.table_name=? "
    "ORDER BY fk.constraint_name"
)


def incoming_constraint(conn: Any, plan: Plan, dialect: str) -> str | None:
    cursor = conn.cursor()
    try:
        if dialect == "snowflake":
            qualified = _sql.qualified(plan.schema_name, plan.table, dialect)
            cursor.execute(f"SHOW EXPORTED KEYS IN TABLE {qualified}")
            names = [column[0].lower() for column in cursor.description]
            rows = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
            return str(rows[0]["fk_name"]) if rows else None
        cursor.execute(INCOMING_DATABRICKS, (plan.schema_name, plan.table))
        row = cursor.fetchone()
        return str(row[0]) if row else None
    finally:
        cursor.close()


def _name(token: str) -> str:
    if token.startswith(('"', "`")):
        return token[1:-1].replace(token[0] * 2, token[0])
    return token


def _columns(tokens: Sequence[str]) -> tuple[str, ...]:
    return tuple(_name(token) for token in tokens if token not in ("(", ")", ","))


def _clauses(ddl: str) -> list[list[str]]:
    tokens = list(_tokens(ddl))
    try:
        start = tokens.index("(")
    except ValueError:
        raise ShapeError("cloud catalog did not return a CREATE TABLE declaration") from None
    clauses: list[list[str]] = []
    current: list[str] = []
    depth = 0
    for token in tokens[start + 1 :]:
        if token == ")" and depth == 0:
            clauses.append(current)
            return clauses
        if token == "," and depth == 0:
            clauses.append(current)
            current = []
        else:
            current.append(token)
            depth += (token == "(") - (token == ")")
    raise ShapeError("cloud catalog CREATE TABLE declaration is incomplete")


def _expression(sql: str, columns: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    keywords = {"TRUE", "FALSE", "NULL", "AND", "OR", "NOT", "IS", "IN", "CAST", "AS"}
    for token in _outer_expression(sql):
        name = _name(token)
        quoted = token.startswith(('"', "`"))
        if name in columns and (
            quoted or (token.upper() not in keywords and token[:1].isidentifier())
        ):
            result.append("column:" + name)
        else:
            result.append(token.upper() if token.upper() in keywords else token)
    return tuple(result)


def verify_existing_constraints(conn: Any, plan: Plan, schema: Any, dialect: str) -> None:
    options = plan.options
    qualified = _sql.qualified(plan.schema_name, plan.table, dialect)
    cursor = conn.cursor()
    try:
        if dialect == "snowflake":
            cursor.execute("SELECT GET_DDL('TABLE', %s)", (qualified,))
        else:
            cursor.execute(f"SHOW CREATE TABLE {qualified}")
        row = cursor.fetchone()
        if row is None:
            raise ShapeError(f"existing table {plan.table!r}: constraint metadata is missing")
        clauses = _clauses(str(row[0]))
        keys: list[tuple[str, ...]] = []
        primary: list[tuple[str, ...]] = []
        foreign: list[tuple[str | None, tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = []
        for clause in clauses:
            constraint_name = None
            if clause and clause[0].upper() == "CONSTRAINT":
                constraint_name = _name(clause[1])
                clause = clause[2:]
            upper = [token.upper() for token in clause]
            if not upper:
                continue
            if upper[0] in ("PRIMARY", "UNIQUE"):
                begin, end = clause.index("("), clause.index(")")
                key_columns = _columns(clause[begin + 1 : end])
                keys.append(key_columns)
                if upper[0] == "PRIMARY":
                    primary.append(key_columns)
            elif upper[:2] == ["FOREIGN", "KEY"]:
                begin, end = clause.index("("), clause.index(")")
                local = _columns(clause[begin + 1 : end])
                referenced = upper.index("REFERENCES") + 1
                target_begin = clause.index("(", referenced)
                target_end = clause.index(")", target_begin)
                parent = tuple(
                    _name(token) for token in clause[referenced:target_begin] if token != "."
                )
                foreign.append(
                    (
                        constraint_name,
                        local,
                        parent,
                        _columns(clause[target_begin + 1 : target_end]),
                    )
                )
        for columns in options.get("unique", []) if dialect == "snowflake" else []:
            if tuple(columns) not in keys:
                raise ShapeError(f"existing table {plan.table!r}: UNIQUE {columns!r} differs")
        if plan.primary_key and tuple(plan.primary_key) not in primary:
            raise ShapeError(f"existing table {plan.table!r}: PRIMARY KEY differs")
        for ref in options.get("foreign_keys", []):
            table = (options.get("table_prefix") or "") + ref["ref_table"]
            expected = (tuple(ref["columns"]), tuple(ref["ref_columns"]))
            matches = [
                parent
                for name, local, parent, target in foreign
                if name == ref["name"]
                and (local, target) == expected
                and parent
                and parent[-1] == table
            ]
            effective_schema = plan.schema_name
            if not effective_schema and any(len(parent) >= 2 for parent in matches):
                cursor.execute("SELECT current_schema()")
                current = cursor.fetchone()
                effective_schema = str(current[0]) if current else None
                if effective_schema is None:
                    matches = []
            if any(len(parent) >= 3 for parent in matches):
                cursor.execute(
                    "SELECT CURRENT_DATABASE()"
                    if dialect == "snowflake"
                    else "SELECT current_catalog()"
                )
                current = cursor.fetchone()
                catalog = str(current[0]) if current else None
                matches = [parent for parent in matches if len(parent) < 3 or parent[-3] == catalog]
            if not matches or (
                effective_schema
                and not any(
                    len(parent) == 1 or parent[-2] == effective_schema for parent in matches
                )
            ):
                raise ShapeError(
                    f"existing table {plan.table!r}: foreign key {ref['name']!r} differs"
                )
        if dialect == "databricks" and options.get("checks"):
            cursor.execute(f"SHOW TBLPROPERTIES {qualified}")
            properties = dict(cursor.fetchall())
            for check in options["checks"]:
                expression = render_expression(
                    check["expression"],
                    quote=lambda value: _sql.quote(value, dialect),
                    literal=lambda value: literal(value, dialect),
                    columns=schema.names,
                )
                actual = properties.get("delta.constraints." + check["name"].lower())
                if actual is None or _expression(actual, schema.names) != _expression(
                    expression, schema.names
                ):
                    raise ShapeError(
                        f"existing table {plan.table!r}: CHECK {check['name']!r} differs"
                    )
        if dialect == "snowflake" and options.get("defaults"):
            cursor.execute(f"DESCRIBE TABLE {qualified}")
            names = [column[0].lower() for column in cursor.description]
            columns = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
            defaults = {column["name"]: column["default"] for column in columns}
            for column, value in options["defaults"].items():
                actual = defaults.get(column)
                expected_literal = literal(value, dialect)
                if actual is None and value is None:
                    continue
                if actual is None or _tokens(str(actual)) != _tokens(expected_literal):
                    raise ShapeError(
                        f"existing table {plan.table!r}: DEFAULT for {column!r} differs"
                    )
    finally:
        cursor.close()
