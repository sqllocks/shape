"""DDL for a designed schema, through the SQL sink's table emitter (so a column type is
spelled the same way in a design and in a generated INSERT script), for the sink's dialects.

``CREATE TABLE`` statements first, then one ``ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY``
per foreign key, so table order never matters. On Fabric Warehouse, which does not enforce
constraints, foreign keys are declared ``NOT ENFORCED``. The text depends on the design and the
options only: no timestamp, no version, so the same design gives the same bytes.
"""

from __future__ import annotations

from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.design.model import DesignError
from shape.design.result import SchemaDesign, Table
from shape.plugins.host import default_host

DIALECTS = ("tsql", "tsql-fabric-warehouse", "postgres", "mysql")


def _quote(name: str, dialect: str) -> str:
    if dialect == "postgres":
        return '"' + name.replace('"', '""') + '"'
    if dialect == "mysql":
        return "`" + name.replace("`", "``") + "`"
    return "[" + name.replace("]", "]]") + "]"


def emit_ddl(
    design: SchemaDesign,
    dialect: str = "tsql",
    *,
    schema_name: str | None = None,
    drop: bool = False,
    go: bool = True,
) -> str:
    """The DDL script for ``design``. ``drop`` adds a ``DROP TABLE`` before each ``CREATE``;
    ``go`` writes ``GO`` batch separators for T-SQL."""
    if dialect not in DIALECTS:
        raise DesignError(f"unknown SQL dialect {dialect!r}; choose one of {', '.join(DIALECTS)}")
    sink = default_host().get("shape.sinks", "sql")
    fabric = dialect == "tsql-fabric-warehouse"
    base = "tsql" if fabric else dialect
    use_go = go and base == "tsql"

    def q(name: str) -> str:
        return _quote(name, base)

    def qualified(table: str) -> str:
        return (f"{q(schema_name)}." if schema_name else "") + q(table)

    options: dict[str, Any] = {"drop": drop}
    parts = [f"-- Schema design '{_one_line(design.name)}' ({design.mode})"]
    for t in design.tables:
        parts.append(
            f"-- {t.kind}: {_one_line(t.name)}\n"
            + _create(sink, t, qualified(t.name), base, fabric, options, use_go)
        )
    foreign: list[str] = []
    for t in design.tables:
        for fk in t.foreign_keys:
            name = f"FK_{t.name}_{'_'.join(fk.columns)}"
            stmt = (
                f"ALTER TABLE {qualified(t.name)} ADD CONSTRAINT {q(name)} "
                f"FOREIGN KEY ({', '.join(q(c) for c in fk.columns)}) "
                f"REFERENCES {qualified(fk.ref_table)} ({', '.join(q(c) for c in fk.ref_columns)})"
                f"{' NOT ENFORCED' if fabric else ''};"
            )
            foreign.append(stmt + ("\nGO" if use_go else ""))
    if foreign:
        parts.append("-- foreign keys\n" + "\n".join(foreign))
    return "\n\n".join(parts) + "\n"


def _create(
    sink: Any,
    table: Table,
    qualified: str,
    base: str,
    fabric: bool,
    options: dict[str, Any],
    use_go: bool,
) -> str:
    schema = pa.schema([pa.field(c.name, pa.string()) for c in table.columns])
    meta = {
        c.name: {
            k: v
            for k, v in (
                ("type", c.type),
                ("nullable", c.nullable),
                ("max_length", c.max_length),
                ("precision", c.precision),
                ("scale", c.scale),
            )
            if v is not None
        }
        for c in table.columns
    }
    text: str = sink._ddl(
        table.name, qualified, schema, meta, list(table.primary_key), base, fabric, options, use_go
    )
    return text


def _one_line(text: str) -> str:
    return " ".join(str(text).splitlines())
