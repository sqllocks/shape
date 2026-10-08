"""W5-04 item 3 (``ddl``): CREATE TABLE with the constraints a contract can state."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from shape.contracts.emit import emit
from shape.contracts.emit.ddl import DIALECTS
from shape.generation.ddl import from_ddl

ALL = ("tsql", "tsql-fabric-warehouse", "postgres", "mysql")


def ddl(contract: dict[str, Any], dialect: str = "tsql", **kw: Any):  # type: ignore[no-untyped-def]
    return emit(contract, "ddl", dialect=dialect, **kw)


def rules(result: Any) -> set[tuple[Any, str]]:
    return {(i["column"], i["rule"]) for i in result.not_expressed}


def test_dialects_are_the_sql_sink_dialects() -> None:
    from shape.builtins.sinks.sql import DIALECTS as SINK

    assert tuple(DIALECTS) == tuple(SINK) == ALL


@pytest.mark.parametrize("dialect", ALL)
def test_ddl_parses_with_the_existing_from_ddl_parser(orders: dict[str, Any], dialect: str) -> None:
    schema, _ = from_ddl(ddl(orders, dialect, table="orders").text, smart=False)
    assert list(schema.tables) == ["orders"]
    assert list(schema.tables["orders"].columns) == list(orders["columns"])


@pytest.mark.parametrize("dialect", ALL)
def test_a_tables_contract_parses_for_every_dialect(
    two_tables: dict[str, Any], dialect: str
) -> None:
    schema, _ = from_ddl(ddl(two_tables, dialect).text, smart=False)
    assert sorted(schema.tables) == ["customers", "orders"]


def test_tsql_text(orders: dict[str, Any]) -> None:
    text = ddl(orders, table="orders").text
    assert "CREATE TABLE [orders] (" in text
    assert "[order_id]" in text and "BIGINT" in text and "NOT NULL" in text
    assert "CONSTRAINT [UQ_orders_order_id] UNIQUE ([order_id])" in text
    assert "CONSTRAINT [CK_orders_order_id_range] CHECK ([order_id] >= 1)" in text
    assert "CONSTRAINT [CK_orders_amount_range] CHECK ([amount] BETWEEN 0 AND 100000)" in text
    assert (
        "CONSTRAINT [CK_orders_status_values] CHECK "
        "([status] IN (N'new', N'paid', N'shipped', N'cancelled'))"
    ) in text
    assert "\nGO\n" in text
    assert "DROP TABLE" not in text
    discount = next(line for line in text.splitlines() if "[discount_code]" in line)
    assert discount.rstrip(",").endswith(" NULL") and "NOT NULL" not in discount


def test_postgres_and_mysql_quote_and_spell_types_like_the_sink(orders: dict[str, Any]) -> None:
    pg = ddl(orders, "postgres", table="orders").text
    assert 'CREATE TABLE "orders" (' in pg
    assert '"amount"' in pg and "DOUBLE PRECISION" in pg and "BOOLEAN" in pg and "TIMESTAMP" in pg
    assert 'CHECK ("amount" BETWEEN 0 AND 100000)' in pg
    assert "'new', 'paid'" in pg and "GO" not in pg
    my = ddl(orders, "mysql", table="orders").text
    assert "CREATE TABLE `orders` (" in my
    assert "DOUBLE" in my and "TINYINT(1)" in my and "DATETIME(6)" in my
    assert "CHECK (`amount` BETWEEN 0 AND 100000)" in my and "GO" not in my


def test_fabric_declares_constraints_not_enforced_and_has_no_check(orders: dict[str, Any]) -> None:
    result = ddl(orders, "tsql-fabric-warehouse", table="orders")
    assert "CONSTRAINT [UQ_orders_order_id] UNIQUE NONCLUSTERED ([order_id]) NOT ENFORCED" in (
        result.text
    )
    assert "CHECK" not in result.text and "DATETIME2(6)" in result.text
    assert {(None if c is None else c, r) for c, r in rules(result)} >= {
        ("order_id", "min"),
        ("amount", "min"),
        ("amount", "max"),
        ("status", "allowed_values"),
    }
    reason = next(i["reason"] for i in result.not_expressed if i["rule"] == "allowed_values")
    assert "CHECK" in reason and "Fabric" in reason
    # NOT NULL is enforced on Fabric and stays
    assert "NOT NULL" in result.text


@pytest.mark.parametrize("dialect", ALL)
def test_what_a_database_cannot_state(orders: dict[str, Any], dialect: str) -> None:
    got = rules(ddl(orders, dialect))
    assert {
        ("discount_code", "max_null_rate"),
        ("amount", "distribution"),
        ("is_gift", "min_true_rate"),
        ("is_gift", "max_true_rate"),
        (None, "row_count"),
        (None, "fd"),
    } <= got


def test_pattern_is_a_check_where_the_dialect_has_a_regular_expression(
    orders: dict[str, Any],
) -> None:
    assert ("customer_email", "pattern") in rules(ddl(orders, "tsql"))
    assert ("customer_email", "pattern") in rules(ddl(orders, "tsql-fabric-warehouse"))
    pg = ddl(orders, "postgres", table="orders")
    assert ("customer_email", "pattern") not in rules(pg)
    # an E'' literal reads the same whatever standard_conforming_strings says (#285)
    assert 'CHECK ("customer_email" ~ E\'^[a-zA-Z0-9._%+\\\\-]+@' in pg.text
    my = ddl(orders, "mysql", table="orders")
    assert ("customer_email", "pattern") not in rules(my)
    assert "CHECK (`customer_email` REGEXP '^[a-zA-Z0-9._%+\\\\-]+@" in my.text


@pytest.mark.parametrize("dialect", ("postgres", "mysql"))
def test_every_pattern_label_gives_ddl_the_parser_reads(dialect: str) -> None:
    from shape.contracts.emit._common import PATTERNS

    contract = {
        "columns": {f"c_{label}": {"dtype": "string", "pattern": label} for label in PATTERNS}
    }
    result = ddl(contract, dialect)
    assert result.not_expressed == []
    schema, _ = from_ddl(result.text, smart=False)
    assert len(schema.tables["table"].columns) == len(PATTERNS)


def test_an_unknown_pattern_is_not_expressed(orders: dict[str, Any]) -> None:
    c = copy.deepcopy(orders)
    c["columns"]["customer_email"]["pattern"] = "no-such-label"
    for dialect in ALL:
        assert ("customer_email", "pattern") in rules(ddl(c, dialect))


def test_one_sided_and_boundary_ranges() -> None:
    c = {
        "columns": {
            "lo": {"dtype": "integer", "min": 0},
            "hi": {"dtype": "integer", "max": 9},
            "eq": {"dtype": "integer", "min": 5, "max": 5},
            "neg": {"dtype": "float", "min": -1.5, "max": 1e-07},
        }
    }
    text = ddl(c, "postgres").text
    assert '"lo" >= 0' in text and '"hi" <= 9' in text
    assert '"eq" BETWEEN 5 AND 5' in text
    assert '"neg" BETWEEN -1.5 AND 1e-07' in text


def test_a_bound_that_is_not_a_number_is_not_expressed() -> None:
    c = {"columns": {"d": {"dtype": "date", "min": "2020-01-01", "max": 3}, "b": {"min": True}}}
    result = ddl(c, "postgres")
    assert ("d", "min") in rules(result) and ("b", "min") in rules(result)
    assert '"d" <= 3' in result.text and "2020" not in result.text


def test_allowed_values_quoting_nulls_and_empty_sets() -> None:
    c = {
        "columns": {
            "s": {"dtype": "string", "allowed_values": ["O'Brien", "a,b", "x)", None]},
            "n": {"dtype": "integer", "allowed_values": [1, 2]},
            "e": {"dtype": "string", "allowed_values": []},
            "b": {"dtype": "boolean", "allowed_values": [True]},
        }
    }
    for dialect in ALL:
        result = ddl(c, dialect)
        assert ("e", "allowed_values") in rules(result)
        if dialect != "tsql-fabric-warehouse":
            assert ("s", "allowed_values") not in rules(result)
        schema, _ = from_ddl(result.text, smart=False)
        assert list(schema.tables["table"].columns) == ["s", "n", "e", "b"]
    pg = ddl(c, "postgres").text
    assert "IN ('O''Brien', 'a,b', 'x)')" in pg and "IN (1, 2)" in pg and "IN (TRUE)" in pg
    assert "IN ()" not in pg
    assert "IN (N'O''Brien', N'a,b', N'x)')" in ddl(c, "tsql").text
    assert "'O\\'Brien'" not in ddl(c, "mysql").text


def test_long_text_values_widen_the_column() -> None:
    c = {"columns": {"s": {"dtype": "string", "allowed_values": ["x" * 300]}}}
    assert "VARCHAR(300)" in ddl(c, "postgres").text
    assert "VARCHAR(255)" in ddl({"columns": {"s": {"dtype": "string"}}}, "postgres").text


def test_identifiers_are_quoted_and_long_constraint_names_are_shortened_deterministically() -> None:
    c = {"columns": {"a]b": {"dtype": "integer", "unique": True}}}
    assert "[a]]b]" in ddl(c).text
    long_name = "c" * 70
    c = {"columns": {long_name: {"dtype": "integer", "unique": True, "min": 1}}}
    first = ddl(c, "postgres", table="t" * 10).text
    assert first == ddl(c, "postgres", table="t" * 10).text
    for line in first.splitlines():
        if "CONSTRAINT" in line:
            assert len(line.split('"')[1]) <= 63
    names = [line.split('"')[1] for line in first.splitlines() if "CONSTRAINT" in line]
    assert len(names) == len(set(names)) == 2


def test_a_column_with_no_dtype_gets_a_guessed_type_and_a_note() -> None:
    c = {
        "required_columns": ["only_required"],
        "columns": {"n": {"min": 1, "max": 2}, "i": {"min": 1.5}, "s": {"unique": True}},
    }
    result = ddl(c, "postgres")
    assert {("n", "dtype"), ("i", "dtype"), ("s", "dtype"), ("only_required", "dtype")} <= rules(
        result
    )
    schema, _ = from_ddl(result.text, smart=False)
    assert list(schema.tables["table"].columns) == ["n", "i", "s", "only_required"]
    assert "BIGINT" in result.text and "DOUBLE PRECISION" in result.text


def test_an_unknown_dtype_is_not_expressed_and_falls_back() -> None:
    result = ddl({"columns": {"x": {"dtype": "object"}}}, "postgres")
    assert ("x", "dtype") in rules(result) and "VARCHAR(255)" in result.text


def test_rules_that_say_nothing_are_not_reported() -> None:
    c = {
        "allow_extra_columns": True,
        "columns": {"a": {"dtype": "integer", "nullable": True, "unique": False}},
    }
    result = ddl(c, "postgres")
    assert result.not_expressed == []
    assert "UNIQUE" not in result.text and "NOT NULL" not in result.text


def test_row_count_is_reported_per_table(two_tables: dict[str, Any]) -> None:
    items = ddl(two_tables).not_expressed
    assert {"table": "orders", "column": None, "rule": "row_count"} == {
        k: v for k, v in next(i for i in items if i["rule"] == "row_count").items() if k != "reason"
    }


def test_a_backslash_is_doubled_for_mysql_and_in_a_postgres_e_literal() -> None:
    c = {"columns": {"s": {"dtype": "string", "allowed_values": ["a\\b"]}}}
    assert "IN ('a\\\\b')" in ddl(c, "mysql").text
    # E'' reads the same whatever standard_conforming_strings says (#285)
    assert "IN (E'a\\\\b')" in ddl(c, "postgres").text


def test_shortened_constraint_names_keep_their_suffix() -> None:
    """The SHA-1 suffix is marked ``usedforsecurity=False`` (bandit B324); names are unchanged."""
    from shape.contracts.emit.ddl import _constraint_name

    long = _constraint_name("ck", "t" * 80, "c" * 80, "postgres")
    assert long == "ck_" + "t" * 51 + "_0578a886"
    assert _constraint_name("ck", "orders", "amount", "postgres") == "ck_orders_amount"
