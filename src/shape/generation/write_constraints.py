"""Validate derived write constraints and render their distinct DDL phases.

Rows remain driver parameters. The literals here are only the explicitly allowed
schema defaults and structured CHECK operands; SQL expressions are never accepted.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from shape.generation.sql_constraints import render_expression
from shape.security.sqltext import check_identifier, postgres_text, tsql_text

_DIALECTS = {
    "postgres",
    "mysql",
    "duckdb",
    "tsql",
    "tsql-fabric-warehouse",
    "snowflake",
    "databricks",
}


@dataclass(frozen=True)
class ConstraintDDL:
    """Column defaults, CREATE clauses, and later ALTER clauses, kept separate."""

    definitions: tuple[str, ...]
    after_create: tuple[str, ...]
    foreign_keys: tuple[str, ...]
    defaults: Mapping[str, str]
    notes: tuple[str, ...]


def identifier(name: Any, dialect: str) -> str:
    """Quote a checked identifier without letting a script acquire extra lines."""
    if not isinstance(name, str) or not name or "\x00" in name:
        raise ValueError("constraint identifiers must be non-empty names without NUL")
    check_identifier(name)
    limit = {"postgres": 63, "mysql": 64, "tsql": 128, "tsql-fabric-warehouse": 128}.get(
        dialect, 255
    )
    size = len(name.encode("utf-8")) if dialect == "postgres" else len(name)
    if size > limit:
        raise ValueError(f"constraint identifier {name!r} exceeds {dialect}'s {limit} limit")
    if dialect in {"mysql", "databricks"}:
        return "`" + name.replace("`", "``") + "`"
    if dialect.startswith("tsql"):
        return "[" + name.replace("]", "]]") + "]"
    return '"' + name.replace('"', '""') + '"'


def literal(value: Any, dialect: str) -> str:
    """A finite scalar, with strings always quoted even when they resemble SQL."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return (
            ("TRUE" if value else "FALSE")
            if dialect in {"postgres", "duckdb", "snowflake", "databricks"}
            else str(int(value))
        )
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        if not value.is_finite() or abs(value.adjusted()) > 1000:
            raise ValueError("constraint literals must be finite with bounded exponents")
        return format(value, "f")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("constraint literals must be finite")
        return repr(value)
    if not isinstance(value, str) or "\x00" in value:
        raise ValueError("constraint defaults must be finite scalar literals, never SQL objects")
    if dialect == "mysql":
        # Charset-tagged hexadecimal text keeps the same value with and without
        # NO_BACKSLASH_ESCAPES and cannot acquire SQL from its contents.
        return "_utf8mb4 X'" + value.encode("utf-8").hex() + "'"
    escaped = value.replace("'", "''")
    if dialect == "postgres":
        return postgres_text(escaped)
    if dialect.startswith("tsql"):
        return tsql_text(escaped, national=dialect == "tsql")
    if dialect in {"snowflake", "databricks"}:
        escaped = escaped.replace("\\", "\\\\")
    return "'" + escaped + "'"


def compile_constraints(
    options: Mapping[str, Any],
    *,
    columns: Sequence[str],
    table: str,
    dialect: str,
    schema_name: str | None = None,
    table_prefix: str = "",
) -> ConstraintDDL:
    """Build supported constraints, refusing malformed data before any connection.

    Foreign keys are separate from other definitions for the post-create phase.
    The Owner-approved DuckDB exception consumes them inline in parent-first
    CREATE statements, while still preparing every table before loading rows.
    """
    if dialect not in _DIALECTS:
        raise ValueError(f"unsupported constraint dialect {dialect!r}")
    known = set(columns)

    def q(value: Any) -> str:
        return identifier(value, dialect)

    q(table)
    if schema_name is not None:
        q(schema_name)
    definitions: list[str] = []
    after: list[str] = []
    foreign: list[str] = []
    defaults: dict[str, str] = {}
    notes: list[str] = []
    used_names: set[str] = set()

    def note(kind: str) -> None:
        text = f"{dialect} cannot hold {kind} constraints; they are not written"
        if text not in notes:
            notes.append(text)

    def items(key: str) -> list[Any]:
        value = options.get(key, [])
        if not isinstance(value, list) or len(value) > 1024:
            raise ValueError(f"{key} must be a list of at most 1024 declarations")
        return value

    def name(value: Any) -> str:
        quoted = q(value)
        if value in used_names:
            raise ValueError(f"duplicate constraint name {value!r}")
        used_names.add(value)
        return quoted

    def names(value: Any, *, local: bool = True) -> str:
        if not isinstance(value, list) or not value or len(value) > 1024:
            raise ValueError("constraint columns must be a non-empty list")
        rendered = [q(v) for v in value]
        if len(set(value)) != len(value) or (local and any(v not in known for v in value)):
            raise ValueError("constraint columns must be distinct declared columns")
        return ", ".join(rendered)

    for index, values in enumerate(items("unique")):
        cols = names(values)
        if dialect == "databricks":
            note("UNIQUE")
        elif dialect == "tsql-fabric-warehouse":
            after.append(
                f"CONSTRAINT {name(f'uq_{table}_{index}')} "
                f"UNIQUE NONCLUSTERED ({cols}) NOT ENFORCED"
            )
        else:
            suffix = " NOT ENFORCED" if dialect == "snowflake" else ""
            definitions.append(f"UNIQUE ({cols}){suffix}")
    for check in items("checks"):
        if not isinstance(check, Mapping) or set(check) != {"name", "expression"}:
            raise ValueError("a CHECK requires only name and a structured expression")
        expression = render_expression(
            check["expression"], quote=q, literal=lambda v: literal(v, dialect), columns=columns
        )
        clause = f"CONSTRAINT {name(check['name'])} CHECK {expression}"
        if dialect in {"snowflake", "tsql-fabric-warehouse"}:
            note("CHECK")
        elif dialect == "databricks":
            after.append(clause)
        else:
            definitions.append(clause)
    values = options.get("defaults", {})
    if not isinstance(values, Mapping) or len(values) > 1024:
        raise ValueError("defaults must map declared columns to finite scalar literals")
    for column, value in values.items():
        q(column)
        if column not in known:
            raise ValueError(f"default names unknown column {column!r}")
        text = literal(value, dialect)
        if dialect in {"databricks", "tsql-fabric-warehouse"}:
            note("DEFAULT")
        else:
            defaults[column] = text
    for ref in items("foreign_keys"):
        if not isinstance(ref, Mapping) or set(ref) != {
            "name",
            "columns",
            "ref_table",
            "ref_columns",
        }:
            raise ValueError("a foreign key requires name, columns, ref_table and ref_columns")
        cols = names(ref["columns"])
        targets = names(ref["ref_columns"], local=False)
        if len(ref["columns"]) != len(ref["ref_columns"]):
            raise ValueError("foreign key and referenced columns need equal arity")
        parent = (
            q(table_prefix + ref["ref_table"]) if isinstance(ref["ref_table"], str) else q(None)
        )
        parent = (q(schema_name) + "." if schema_name is not None else "") + parent
        suffix = (
            " NOT ENFORCED"
            if dialect in {"snowflake", "databricks", "tsql-fabric-warehouse"}
            else ""
        )
        foreign.append(
            f"CONSTRAINT {name(ref['name'])} FOREIGN KEY ({cols}) "
            f"REFERENCES {parent} ({targets}){suffix}"
        )
    return ConstraintDDL(tuple(definitions), tuple(after), tuple(foreign), defaults, tuple(notes))
