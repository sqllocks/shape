"""W5-02 item 3: DDL for the existing dialects, read back by the existing DDL parser."""

from __future__ import annotations

from typing import Any

import pytest

from shape.builtins.sinks.sql import DIALECTS
from shape.design import DesignError, DesignInput
from shape.design.ddl import DIALECTS as DESIGN_DIALECTS
from shape.design.ddl import emit_ddl
from shape.design.engine import derive
from shape.generation.ddl import from_ddl


def result(doc: dict[str, Any], mode: str):  # type: ignore[no-untyped-def]
    return derive(DesignInput.from_dict(doc), mode)


def test_dialects_are_the_sql_sink_dialects() -> None:
    assert DESIGN_DIALECTS == DIALECTS


@pytest.mark.parametrize("dialect", ["tsql", "postgres", "mysql"])
@pytest.mark.parametrize("mode", ["3nf", "star", "snowflake"])
def test_ddl_round_trips_through_the_ddl_parser(
    doc: dict[str, Any], dialect: str, mode: str
) -> None:
    design = result(doc, mode)
    sql = emit_ddl(design, dialect)
    schema, _ = from_ddl(sql, smart=False)
    assert sorted(schema.tables) == sorted(t.name for t in design.tables)
    for t in design.tables:
        parsed = schema.tables[t.name]
        assert list(parsed.columns) == [c.name for c in t.columns]
        assert tuple(parsed.primary_key) == t.primary_key
    declared = {(t.name, f.columns[0], f.ref_table) for t in design.tables for f in t.foreign_keys}
    found = {(r.child, r.child_columns[0], r.parent) for r in schema.relationships}
    assert declared <= found


def test_tsql_ddl_shape(doc: dict[str, Any]) -> None:
    sql = emit_ddl(result(doc, "star"), "tsql", schema_name="dw")
    assert "CREATE TABLE [dw].[fact_sales] (" in sql
    assert "CONSTRAINT [PK_fact_sales] PRIMARY KEY ([order_id], [line_no])" in sql
    assert "ALTER TABLE [dw].[fact_sales] ADD CONSTRAINT [FK_fact_sales_sk_customer]" in sql
    assert "FOREIGN KEY ([sk_customer]) REFERENCES [dw].[dim_customer] ([sk_customer]);" in sql
    assert "\nGO\n" in sql
    assert "DECIMAL(18,2)" in sql
    assert "DROP TABLE" not in sql


def test_drop_statements_are_opt_in(doc: dict[str, Any]) -> None:
    sql = emit_ddl(result(doc, "3nf"), "postgres", drop=True)
    assert 'DROP TABLE IF EXISTS "customer" CASCADE;' in sql


def test_fabric_foreign_keys_are_not_enforced(doc: dict[str, Any]) -> None:
    sql = emit_ddl(result(doc, "star"), "tsql-fabric-warehouse")
    assert "NOT ENFORCED;" in sql
    assert "does not enforce PRIMARY KEY" in sql
    assert "ADD CONSTRAINT [FK_fact_sales_sk_customer] FOREIGN KEY" in sql


def test_mysql_and_postgres_quoting(doc: dict[str, Any]) -> None:
    d = result(doc, "star")
    assert "CREATE TABLE `fact_sales` (" in emit_ddl(d, "mysql")
    assert 'CREATE TABLE "fact_sales" (' in emit_ddl(d, "postgres")


def test_unknown_dialect_is_rejected(doc: dict[str, Any]) -> None:
    with pytest.raises(DesignError, match="dialect"):
        emit_ddl(result(doc, "3nf"), "oracle")


def test_ddl_has_no_timestamp_or_version(doc: dict[str, Any]) -> None:
    sql = emit_ddl(result(doc, "3nf"), "tsql")
    assert sql.startswith("-- Schema design 'retail' (3nf)\n")
    assert "Generated" not in sql
