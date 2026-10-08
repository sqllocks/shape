"""P4-01c: the five ``from-ddl`` fixes (owner decision of 2026-10-01), one block each.

1. a column-level ``REFERENCES`` is a foreign key;
2. ``VARBINARY(MAX)``, ``BINARY(MAX)`` and the BLOB types are binary;
3. names match whole words (``discount_pct``, ``state``, ``model``, ``catalog``, ...);
4. ``gender CHAR(1)`` and other one-character codes get a value set;
5. CR-08: a parent total is the sum over its children.

Round 2 (the lead's decision, same day):

6. F6: a key the DDL does not declare, guessed by name, points at the parent's primary key;
7. F7: generated strings never exceed the declared length (``CHAR(2)`` codes included);
8. F8: ``CustomerId``/``CustomerID`` is read like ``customer_id``.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest
from engine_fixtures import RowFK, Seq

from shape.builtins.strategies import providers
from shape.generation import ddl_infer
from shape.generation.ddl import DdlParser, from_ddl
from shape.generation.ddl_infer import ColumnSemantic as S
from shape.generation.ddl_infer import TableRole
from shape.generation.ddl_names import snake, word_pattern, words
from shape.generation.engine import Engine
from shape.generation.schema import Column, GenSchema, Table
from shape.plugins.host import default_host


class Spread:
    """Any numeric ``distribution`` column: a repeating ramp of two-decimal values."""

    name = "distribution"

    def generate(self, spec: object, ctx: object) -> pa.Array:
        rows = np.arange(ctx.row_start, ctx.row_start + ctx.n_rows)  # type: ignore[attr-defined]
        return pa.array(np.round(1 + (rows % 50) * 1.37, 2))


class Pick:
    """A ``weighted_enum`` column: the values in turn."""

    name = "weighted_enum"

    def generate(self, spec: dict[str, object], ctx: object) -> pa.Array:
        values = list(spec["values"])  # type: ignore[call-overload]
        rows = range(ctx.row_start, ctx.row_start + ctx.n_rows)  # type: ignore[attr-defined]
        return pa.array([values[i % len(values)] for i in rows])


STRATEGIES = {s.name: s for s in (Seq(), Pick(), RowFK(), Spread())}


def generate(schema: GenSchema, seed: int = 1):  # noqa: ANN201
    return Engine(schema, strategies=STRATEGIES, seed=seed).generate()


PARENT = "CREATE TABLE customer (id INT PRIMARY KEY, name VARCHAR(40));\n"


def errors(schema: GenSchema) -> list[str]:
    return [f"{i.location}: {i.message}" for i in schema.validate() if i.level == "error"]


def column(schema: GenSchema, table: str, name: str) -> Column:
    return schema.tables[table].columns[name]


# ---- 1. column-level REFERENCES -------------------------------------------------------------

INLINE_REFERENCES = {
    "plain": "customer_id INT REFERENCES customer(id)",
    "not null": "customer_id INTEGER NOT NULL REFERENCES customer (id)",
    "on delete": "customer_id INT REFERENCES customer(id) ON DELETE CASCADE ON UPDATE NO ACTION",
    "constraint": "customer_id INT CONSTRAINT fk_o_c REFERENCES customer(id)",
    "constraint, not null, on delete": (
        "customer_id INT NOT NULL CONSTRAINT fk_o_c REFERENCES customer(id) ON DELETE SET NULL"
    ),
    "sql server": "[customer_id] INT NOT NULL REFERENCES [dbo].[customer] ([id])",
    "mysql": "`customer_id` INT NULL REFERENCES `customer`(`id`)",
    "quoted": 'customer_id INT REFERENCES "customer"("id") ON DELETE CASCADE',
    "key left out": "customer_id INT REFERENCES customer",
    "lower case": "customer_id int references customer(id)",
    "default first": "customer_id INT DEFAULT 1 REFERENCES customer(id)",
}


@pytest.mark.parametrize("clause", INLINE_REFERENCES.values(), ids=INLINE_REFERENCES)
@pytest.mark.parametrize("smart", [True, False], ids=["smart", "plain"])
def test_a_column_level_references_is_a_foreign_key(clause: str, smart: bool) -> None:
    sql = PARENT + f"CREATE TABLE orders (id INT PRIMARY KEY, {clause});"
    schema, _ = from_ddl(sql, smart=smart)
    col = column(schema, "orders", "customer_id")
    assert col.generator["strategy"] == "foreign_key"
    assert col.generator["ref"] == "customer.id"
    (rel,) = schema.relationships
    assert (rel.parent, rel.child) == ("customer", "orders")
    assert (rel.parent_columns, rel.child_columns) == (["id"], ["customer_id"])
    assert errors(schema) == []


def test_the_inline_fixture_validates_and_generates() -> None:
    """The baseline fixture ``e2e_cli__inline``: its key points at ``customer.id``."""
    sql = (
        "CREATE TABLE customer (id INT PRIMARY KEY, rank_no INT);\n"
        "CREATE TABLE orders (id INT PRIMARY KEY, customer_id INT REFERENCES customer(id));"
    )
    schema, _ = from_ddl(sql, scale="small:customer=20,orders=60")
    assert errors(schema) == []
    result = generate(schema, 3)
    parent_ids = set(result["customer"]["id"].to_pylist())
    child_ids = {v for v in result["orders"]["customer_id"].to_pylist() if v is not None}
    assert child_ids and child_ids <= parent_ids


def test_the_key_may_name_a_column_that_is_not_the_parents_key_name() -> None:
    sql = (
        "CREATE TABLE region (region_code VARCHAR(5) PRIMARY KEY);\n"
        "CREATE TABLE shop (id INT PRIMARY KEY, home INT REFERENCES region(region_code));"
    )
    schema, _ = from_ddl(sql)
    assert column(schema, "shop", "home").generator["ref"] == "region.region_code"


def test_a_key_clause_without_columns_means_the_parents_primary_key() -> None:
    sql = (
        "CREATE TABLE customer (cid INT PRIMARY KEY);\n"
        "CREATE TABLE orders (id INT PRIMARY KEY, who INT REFERENCES customer);"
    )
    schema, _ = from_ddl(sql)
    assert column(schema, "orders", "who").generator["ref"] == "customer.cid"


def test_a_key_clause_without_columns_on_a_parent_without_one_is_not_read() -> None:
    sql = "CREATE TABLE a (x INT);\nCREATE TABLE b (id INT PRIMARY KEY, who INT REFERENCES a);"
    schema, _ = from_ddl(sql)
    assert schema.relationships == []
    assert column(schema, "b", "who").generator["strategy"] != "foreign_key"


def test_a_column_level_self_reference_is_self_referencing() -> None:
    sql = "CREATE TABLE node (id INT PRIMARY KEY, parent_id INT REFERENCES node(id));"
    schema, _ = from_ddl(sql)
    assert column(schema, "node", "parent_id").generator["strategy"] == "self_referencing"
    assert errors(schema) == []


def test_a_named_constraint_does_not_change_the_columns_type() -> None:
    sql = PARENT + (
        "CREATE TABLE orders (id INT CONSTRAINT pk_o PRIMARY KEY, "
        "customer_id INT CONSTRAINT fk_o_c REFERENCES customer(id));"
    )
    schema = DdlParser().parse_string(sql)
    assert column(schema, "orders", "customer_id").type == "integer"
    assert column(schema, "orders", "id").type == "integer"


def test_a_string_literal_that_says_references_is_not_a_key() -> None:
    sql = (
        PARENT
        + "CREATE TABLE t (id INT PRIMARY KEY, note VARCHAR(30) DEFAULT 'references customer(id)');"
    )
    schema, _ = from_ddl(sql)
    assert schema.relationships == []


def test_a_table_level_constraint_still_wins_over_the_convention() -> None:
    sql = PARENT + (
        "CREATE TABLE orders (id INT PRIMARY KEY, customer_id INT, "
        "FOREIGN KEY (customer_id) REFERENCES customer(id));"
    )
    schema, _ = from_ddl(sql)
    assert column(schema, "orders", "customer_id").generator["ref"] == "customer.id"


# ---- 2. binary types ------------------------------------------------------------------------

BINARY_TYPES = [
    "VARBINARY(MAX)",
    "varbinary(max)",
    "BINARY(MAX)",
    "VARBINARY",
    "VARBINARY(100)",
    "BINARY(16)",
    "IMAGE",
    "BYTEA",
    "BLOB",
    "BLOB(65535)",
    "TINYBLOB",
    "MEDIUMBLOB",
    "LONGBLOB",
    "BINARY VARYING",
]


@pytest.mark.parametrize("sql_type", BINARY_TYPES)
@pytest.mark.parametrize("smart", [True, False], ids=["smart", "plain"])
def test_binary_columns_are_left_out(sql_type: str, smart: bool) -> None:
    sql = f"CREATE TABLE doc (id INT PRIMARY KEY, title VARCHAR(80), payload {sql_type} NOT NULL);"
    schema, _ = from_ddl(sql, smart=smart)
    assert list(schema.tables["doc"].columns) == ["id", "title"]


def test_max_is_a_length_for_strings_too() -> None:
    schema = DdlParser().parse_string(
        "CREATE TABLE t (id INT, body NVARCHAR(MAX), big VARCHAR(max));"
    )
    for name in ("body", "big"):
        col = column(schema, "t", name)
        assert col.type == "string"
        assert col.max_length is None
        assert col.generator["provider"] == "text"


# ---- 3. whole-word names --------------------------------------------------------------------


def test_words_split_snake_case_and_camel_case() -> None:
    assert words("discount_pct") == ["discount", "pct"]
    assert words("SalesOrderID") == ["sales", "order", "id"]
    assert words("OrderDate") == ["order", "date"]
    assert snake("OrderDate") == "order_date"


def test_a_word_pattern_matches_whole_words_and_plurals_only() -> None:
    pat = word_pattern("count", "category", "num_", "expir*")
    for hit in ("count", "item_counts", "category", "categories", "num_items", "expiry_date"):
        assert pat.search(hit), hit
    for miss in ("discount", "account", "counter", "enum_x", "numeric", "categorical"):
        assert not pat.search(miss), miss


def semantic(name: str, sql_type: str = "DECIMAL(10,2)", table: str = "t") -> S:
    schema = DdlParser().parse_string(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, {name} {sql_type});"
    )
    tbl: Table = schema.tables[table]
    return ddl_infer._classify_column(name, tbl.columns[name], TableRole.UNKNOWN, tbl)


@pytest.mark.parametrize(
    ("name", "sql_type", "expected"),
    [
        # the substring the baseline matched is in the comment
        ("discount_pct", "DECIMAL(5,2)", S.PERCENTAGE),  # count
        ("DiscountPct", "DECIMAL(5,2)", S.PERCENTAGE),
        ("margin_pct", "DECIMAL(5,2)", S.PERCENTAGE),  # (margin: money)
        ("tax_rate", "DECIMAL(5,4)", S.PERCENTAGE),  # (rate: money)
        ("defect_ratio", "FLOAT", S.PERCENTAGE),
        ("current_value", "DECIMAL(10,2)", S.UNKNOWN),  # rent
        ("parent_weight", "DECIMAL(10,2)", S.MEASUREMENT),  # (rent, then weight)
        ("feedback_score", "DECIMAL(3,1)", S.RATING),  # fee
        ("corporate_sales", "DECIMAL(10,2)", S.UNKNOWN),  # rate
        ("discharge_notes", "VARCHAR(100)", S.TEXT_DESCRIPTION),  # charge
        ("account_flag", "INT", S.UNKNOWN),  # count
        ("model", "VARCHAR(40)", S.UNKNOWN),  # mode
        ("hotel", "VARCHAR(40)", S.UNKNOWN),  # tel
        ("prototype", "VARCHAR(40)", S.UNKNOWN),  # type
        ("security_answer", "VARCHAR(40)", S.UNKNOWN),  # uri
        ("overdue_date", "DATE", S.TEMPORAL_GENERIC),  # due_date
        ("preorder_date", "DATE", S.TEMPORAL_GENERIC),  # order_date
        ("restart_date", "DATE", S.TEMPORAL_GENERIC),  # start_date
        ("last_update", "DATETIME", S.TEMPORAL_GENERIC),  # (date in update)
        ("flagship", "VARCHAR(40)", S.UNKNOWN),  # flag
        ("upgrade_level", "INT", S.RATING),  # (grade, then level)
        ("username", "VARCHAR(40)", S.UNKNOWN),  # name
        # the legitimate matches still match
        ("item_count", "INT", S.QUANTITY),
        ("item_counts", "INT", S.QUANTITY),
        ("ItemCount", "INT", S.QUANTITY),
        ("num_items", "INT", S.QUANTITY),
        ("unit_price", "DECIMAL(10,2)", S.MONETARY),
        ("total_amounts", "DECIMAL(10,2)", S.MONETARY),
        ("hourly_rate", "DECIMAL(10,2)", S.MONETARY),
        ("product_category", "VARCHAR(40)", S.CATEGORICAL),
        ("order_priority", "INT", S.RATING),
        ("expiry_date", "DATE", S.TEMPORAL_END),
        ("OrderDate", "DATE", S.TEMPORAL_TRANSACTION),
        ("order_status", "VARCHAR(20)", S.STATUS),
        ("contact_phone", "VARCHAR(20)", S.PHONE),
        ("country_code", "VARCHAR(2)", S.COUNTRY),
        ("tracking_number", "VARCHAR(20)", S.CODE),
    ],
)
def test_names_match_whole_words(name: str, sql_type: str, expected: S) -> None:
    assert semantic(name, sql_type) == expected


def test_a_percentage_column_gets_a_percentage_distribution_and_rule() -> None:
    sql = "CREATE TABLE line (id INT PRIMARY KEY, discount_pct DECIMAL(5,2) NOT NULL DEFAULT 0);"
    schema, notes = from_ddl(sql)
    gen = column(schema, "line", "discount_pct").generator
    assert gen["distribution"] == "normal"
    assert gen["params"]["max"] == 100
    assert {(n.rule_id, n.column) for n in notes} >= {("ND-PERCENTAGE", "discount_pct")}
    assert not any(n.rule_id in ("ND-QUANTITY", "BR-06") for n in notes)
    assert [r.rule for r in schema.business_rules] == ["discount_pct BETWEEN 0 AND 100"]


@pytest.mark.parametrize("smart", [True, False], ids=["smart", "plain"])
def test_a_state_string_is_a_state_not_a_status(smart: bool) -> None:
    schema, notes = from_ddl(
        "CREATE TABLE address (id INT PRIMARY KEY, state VARCHAR(50), status VARCHAR(20));",
        smart=smart,
    )
    state = column(schema, "address", "state").generator
    assert state == {"strategy": "faker", "provider": "state_abbr"}
    assert not any(n.rule_id == "EN-STATUS" and n.column == "state" for n in notes)
    assert column(schema, "address", "status").generator["strategy"] == "weighted_enum"


def test_a_model_column_is_not_a_category_and_a_catalog_is_not_a_log() -> None:
    sql = (
        "CREATE TABLE product (id INT PRIMARY KEY, model VARCHAR(40));\n"
        "CREATE TABLE catalog (id INT PRIMARY KEY, product_id INT REFERENCES product(id));"
    )
    schema, notes = from_ddl(sql)
    assert column(schema, "product", "model").generator["strategy"] == "faker"
    roles = {n.table: n.rule_id for n in notes if n.rule_id.startswith("TC-")}
    assert roles["catalog"] != "TC-LOG"


@pytest.mark.parametrize(
    ("table", "is_log"),
    [("audit_log", True), ("AuditLogs", True), ("order_events", True), ("login", False),
     ("blog_post", False), ("catalogs", False), ("activities", True)],
)  # fmt: skip
def test_log_tables_are_found_by_word(table: str, is_log: bool) -> None:
    sql = f"CREATE TABLE {table} (id INT PRIMARY KEY, msg VARCHAR(100));"
    _, notes = from_ddl(sql)
    assert ("TC-LOG" in {n.rule_id for n in notes}) == is_log


def test_the_measurement_hint_is_found_by_word() -> None:
    sql = "CREATE TABLE t (id INT PRIMARY KEY, resize_height DECIMAL(8,2), box_size DECIMAL(8,2));"
    schema, _ = from_ddl(sql)
    assert column(schema, "t", "resize_height").generator["params"]["mean"] == 170.0
    assert column(schema, "t", "box_size").generator["params"]["mean"] == 10.0


def test_correlated_columns_are_found_by_word() -> None:
    """``network`` is not ``net`` and ``taxi`` is not ``tax``."""
    sql = (
        "CREATE TABLE t (id INT PRIMARY KEY, network_speed DECIMAL(10,2), gross DECIMAL(10,2), "
        "tax_amount DECIMAL(10,2), unit_price DECIMAL(10,2), unit_cost DECIMAL(10,2));\n"
        "CREATE TABLE u (id INT PRIMARY KEY, taxi_price DECIMAL(10,2), subtotal DECIMAL(10,2));"
    )
    schema, notes = from_ddl(sql)
    ids = {(n.rule_id, n.table, n.column) for n in notes}
    assert not any(rule == "CR-05" for rule, _, _ in ids)  # net = gross - tax needs a net column
    assert column(schema, "t", "network_speed").generator["strategy"] == "distribution"
    assert not any(rule == "CR-02" and table == "u" for rule, table, _ in ids)
    assert ("CR-01", "t", "unit_cost") in ids  # the legitimate matches still fire
    assert ("CR-02", "t", "tax_amount") not in ids  # (no subtotal or amount in t besides itself)


# ---- 4. coded single-character columns ------------------------------------------------------

CODES = {
    "gender": {"M", "F"},
    "sex": {"M", "F"},
    "marital_status": {"A", "I", "P"},
    "status": {"A", "I", "P"},
    "is_active": {"Y", "N"},
    "vip_flag": {"Y", "N"},
    "tier_label": {"A", "B", "C"},
    "kind": {"A", "B", "C"},
}


@pytest.mark.parametrize("name", CODES)
@pytest.mark.parametrize("sql_type", ["CHAR(1)", "NCHAR(1)", "VARCHAR(1)", "nvarchar(1)"])
@pytest.mark.parametrize("smart", [True, False], ids=["smart", "plain"])
def test_a_one_character_code_gets_a_value_set(name: str, sql_type: str, smart: bool) -> None:
    schema, _ = from_ddl(f"CREATE TABLE p (id INT PRIMARY KEY, {name} {sql_type});", smart=smart)
    gen = column(schema, "p", name).generator
    assert gen["strategy"] == "weighted_enum"
    assert set(gen["values"]) == CODES[name]
    assert sum(gen["values"].values()) == pytest.approx(1.0)


def test_the_codes_generate_one_character_each() -> None:
    cols = ", ".join(f"{n} CHAR(1)" for n in CODES)
    schema, _ = from_ddl(f"CREATE TABLE p (id INT PRIMARY KEY, {cols});", scale="small:p=300")
    table = generate(schema, 5)["p"]
    for name, allowed in CODES.items():
        values = {v for v in table[name].to_pylist() if v is not None}
        assert values and values <= allowed, name


@pytest.mark.parametrize("sql_type", ["VARCHAR(10)", "VARCHAR(50)", "NVARCHAR(MAX)", "TEXT"])
def test_gender_of_any_length_is_m_or_f(sql_type: str) -> None:
    schema, _ = from_ddl(f"CREATE TABLE p (id INT PRIMARY KEY, gender {sql_type});")
    assert set(column(schema, "p", "gender").generator["values"]) == {"M", "F"}


def test_a_longer_code_without_a_name_rule_keeps_its_pattern() -> None:
    schema, _ = from_ddl("CREATE TABLE p (id INT PRIMARY KEY, currency CHAR(8));")
    assert column(schema, "p", "currency").generator == {"strategy": "pattern", "format": "{seq:6}"}


# ---- 5. CR-08 -------------------------------------------------------------------------------

ORDERS = """\
CREATE TABLE customers (customer_id INT PRIMARY KEY, visits INT);
CREATE TABLE orders (
    order_id INT PRIMARY KEY,
    customer_id INT NOT NULL REFERENCES customers(customer_id),
    order_date DATE,
    total_amount DECIMAL(12,2)
);
CREATE TABLE order_lines (
    line_id INT PRIMARY KEY,
    order_id INT NOT NULL REFERENCES orders(order_id),
    quantity INT,
    unit_price DECIMAL(10,2),
    line_total DECIMAL(12,2)
);
"""


def test_a_parent_total_is_the_sum_over_its_children() -> None:
    schema, notes = from_ddl(ORDERS)
    gen = column(schema, "orders", "total_amount").generator
    assert gen == {
        "strategy": "computed",
        "rule": "sum_children",
        "child_table": "order_lines",
        "child_column": "line_total",
        "output_type": "decimal",  # ISS-gen: the declared DECIMAL(p,s) is kept
    }
    (note,) = [n for n in notes if n.rule_id == "CR-08"]
    assert (note.table, note.column) == ("orders", "total_amount")
    assert errors(schema) == []


def test_the_generated_parent_total_equals_the_sum_of_its_children() -> None:
    schema, _ = from_ddl(ORDERS, scale="small:customers=10,orders=40,order_lines=160")
    result = generate(schema, 11)
    lines = result["order_lines"].to_pandas()
    orders = result["orders"].to_pandas().set_index("order_id")
    sums = lines.groupby("order_id")["line_total"].sum().round(2)
    assert len(sums) > 5
    assert orders.loc[sums.index, "total_amount"].round(2).tolist() == sums.tolist()
    childless = orders.index.difference(sums.index)
    assert (orders.loc[childless, "total_amount"] == 0).all()


def test_cr08_is_in_the_explain_report() -> None:
    _, notes = from_ddl(ORDERS)
    assert re.search(r"CR-08", " ".join(n.rule_id for n in notes))


def test_cr08_is_smart_inference_only() -> None:
    schema, notes = from_ddl(ORDERS, smart=False)
    assert notes == []
    assert column(schema, "orders", "total_amount").generator["strategy"] == "distribution"


def test_cr08_leaves_a_parent_with_two_links_to_the_same_child_alone() -> None:
    sql = ORDERS.replace(
        "order_id INT NOT NULL REFERENCES orders(order_id),",
        "order_id INT NOT NULL REFERENCES orders(order_id),\n"
        "    return_order_id INT REFERENCES orders(order_id),",
    )
    schema, notes = from_ddl(sql)
    assert not any(n.rule_id == "CR-08" for n in notes)
    assert column(schema, "orders", "total_amount").generator["strategy"] == "distribution"


def test_cr08_needs_a_monetary_child_column() -> None:
    sql = (
        "CREATE TABLE orders (order_id INT PRIMARY KEY, total_amount DECIMAL(12,2));\n"
        "CREATE TABLE notes (note_id INT PRIMARY KEY, order_id INT REFERENCES orders(order_id), "
        "body VARCHAR(200));"
    )
    schema, notes = from_ddl(sql)
    assert not any(n.rule_id == "CR-08" for n in notes)
    assert column(schema, "orders", "total_amount").generator["strategy"] == "distribution"


def test_cr08_does_not_chain_totals() -> None:
    """The compute phase fills tables in schema order, so a total over another total is left as
    a plain column (the inner sum still applies)."""
    sql = (
        "CREATE TABLE orders (order_id INT PRIMARY KEY, total DECIMAL(12,2));\n"
        "CREATE TABLE shipments (shipment_id INT PRIMARY KEY, "
        "order_id INT REFERENCES orders(order_id), total DECIMAL(12,2));\n"
        "CREATE TABLE parcels (parcel_id INT PRIMARY KEY, "
        "shipment_id INT REFERENCES shipments(shipment_id), amount DECIMAL(12,2));"
    )
    schema, notes = from_ddl(sql)
    assert column(schema, "shipments", "total").generator["strategy"] == "computed"
    assert column(schema, "orders", "total").generator["strategy"] == "distribution"
    assert [(n.table, n.column) for n in notes if n.rule_id == "CR-08"] == [("shipments", "total")]


def test_cr08_needs_the_child_to_point_at_the_parents_key() -> None:
    sql = (
        "CREATE TABLE orders (order_id INT PRIMARY KEY, order_no INT, total DECIMAL(12,2));\n"
        "CREATE TABLE lines (line_id INT PRIMARY KEY, "
        "order_no INT REFERENCES orders(order_no), amount DECIMAL(12,2));"
    )
    schema, notes = from_ddl(sql)
    assert not any(n.rule_id == "CR-08" for n in notes)
    assert column(schema, "orders", "total").generator["strategy"] == "distribution"


# ---- 6. a guessed key points at the parent's primary key --------------------------------------


class LongText:
    """The ``faker`` strategy's output is cut at the column length by the strategy itself
    (``providers._truncate``); text longer than any column stands in for what Faker returns, so
    the check does not need the optional ``faker`` package."""

    name = "faker"

    def generate(self, spec: object, ctx: object) -> pa.Array:
        values = pa.array(["z" * 400] * ctx.n_rows)  # type: ignore[attr-defined]
        return providers._truncate(values, ctx)


class OwnParent:
    """``self_referencing``: every row points at an earlier row's key (the keys are 1, 2, ...)."""

    name = "self_referencing"

    def generate(self, spec: object, ctx: object) -> pa.Array:
        rows = np.arange(ctx.row_start, ctx.row_start + ctx.n_rows)  # type: ignore[attr-defined]
        return pa.array(np.maximum(rows, 1))


def real_generate(schema: GenSchema, seed: int = 1):  # noqa: ANN201
    """Generation with the built-in strategies. The two key strategies the default registry does
    not serve (``foreign_key``, ``self_referencing``) and ``faker`` are stood in for."""
    host = default_host()
    strategies = {
        name: host.try_get("shape.strategies", name)
        for name in {c.strategy for t in schema.tables.values() for c in t.columns.values()}
    }
    strategies.update({s.name: s for s in (RowFK(), OwnParent(), LongText())})
    return Engine(schema, strategies=strategies, seed=seed).generate()


@pytest.mark.parametrize("smart", [True, False], ids=["smart", "plain"])
@pytest.mark.parametrize("parent", ["customer", "customers"])
def test_a_guessed_key_points_at_the_parents_primary_key(parent: str, smart: bool) -> None:
    sql = (
        f"CREATE TABLE {parent} (id INT PRIMARY KEY, name VARCHAR(40));\n"
        "CREATE TABLE orders (id INT PRIMARY KEY, customer_id INT NOT NULL);"
    )
    schema, _ = from_ddl(sql, smart=smart)
    gen = column(schema, "orders", "customer_id").generator
    assert gen["strategy"] == "foreign_key"
    assert gen["ref"] == f"{parent}.id"
    rel = next(r for r in schema.relationships if r.child == "orders")
    assert (rel.parent, rel.parent_columns, rel.child_columns) == (parent, ["id"], ["customer_id"])
    assert errors(schema) == []


def test_a_guessed_key_names_the_primary_key_whatever_it_is_called() -> None:
    sql = (
        "CREATE TABLE customer (cust_no VARCHAR(10), PRIMARY KEY (cust_no));\n"
        "CREATE TABLE orders (id INT PRIMARY KEY, customer_id VARCHAR(10));"
    )
    schema, _ = from_ddl(sql)
    assert column(schema, "orders", "customer_id").generator["ref"] == "customer.cust_no"
    assert errors(schema) == []


@pytest.mark.parametrize(
    "parent",
    [
        "CREATE TABLE customer (a INT, b INT, name VARCHAR(40), PRIMARY KEY (a, b));",
        "CREATE TABLE customer (a INT, name VARCHAR(40));",
    ],
    ids=["composite key", "no key"],
)
@pytest.mark.parametrize("smart", [True, False], ids=["smart", "plain"])
def test_a_guessed_key_is_not_made_when_the_parent_has_no_single_column_key(
    parent: str, smart: bool
) -> None:
    sql = parent + "\nCREATE TABLE orders (id INT PRIMARY KEY, customer_id INT);"
    schema, _ = from_ddl(sql, smart=smart)
    assert column(schema, "orders", "customer_id").generator["strategy"] != "foreign_key"
    assert [r for r in schema.relationships if r.child == "orders"] == []
    assert errors(schema) == []


def test_a_guessed_key_generates_only_parent_keys() -> None:
    sql = (
        "CREATE TABLE customer (id INT PRIMARY KEY, name VARCHAR(40));\n"
        "CREATE TABLE orders (id INT PRIMARY KEY, customer_id INT NOT NULL);"
    )
    schema, _ = from_ddl(sql, scale="small:customer=30,orders=200")
    result = real_generate(schema, 4)
    parents = set(result["customer"]["id"].to_pylist())
    children = set(result["orders"]["customer_id"].to_pylist())
    assert children and children <= parents


# ---- 8. CamelCase keys (F8) -------------------------------------------------------------------


@pytest.mark.parametrize("name", ["CustomerId", "CustomerID", "customerId", "Customer_Id"])
@pytest.mark.parametrize("smart", [True, False], ids=["smart", "plain"])
def test_a_camel_case_id_is_a_key_like_customer_id(name: str, smart: bool) -> None:
    sql = (
        "CREATE TABLE Customer (Id INT PRIMARY KEY, Name VARCHAR(40));\n"
        f"CREATE TABLE Orders (OrderId INT PRIMARY KEY, {name} INT NOT NULL);"
    )
    schema, _ = from_ddl(sql, smart=smart)
    gen = column(schema, "Orders", name).generator
    assert (gen["strategy"], gen["ref"]) == ("foreign_key", "Customer.Id")
    assert errors(schema) == []


def test_a_camel_case_key_finds_a_camel_case_table_and_a_plural() -> None:
    sql = (
        "CREATE TABLE SalesOrders (Id INT PRIMARY KEY);\n"
        "CREATE TABLE OrderItem (Id INT PRIMARY KEY);\n"
        "CREATE TABLE Line (Id INT PRIMARY KEY, SalesOrderId INT, OrderItemID INT);"
    )
    schema, _ = from_ddl(sql)
    assert column(schema, "Line", "SalesOrderId").generator["ref"] == "SalesOrders.Id"
    assert column(schema, "Line", "OrderItemID").generator["ref"] == "OrderItem.Id"


def test_a_camel_case_key_needs_a_single_column_parent_key() -> None:
    sql = (
        "CREATE TABLE Customer (A INT, B INT, PRIMARY KEY (A, B));\n"
        "CREATE TABLE Orders (Id INT PRIMARY KEY, CustomerId INT);"
    )
    schema, _ = from_ddl(sql)
    assert column(schema, "Orders", "CustomerId").generator["strategy"] != "foreign_key"
    assert errors(schema) == []


@pytest.mark.parametrize("name", ["Id", "ID", "id", "Idea", "Paid", "Valid", "orderdate"])
def test_a_name_that_only_ends_in_id_is_not_a_key(name: str) -> None:
    sql = (
        "CREATE TABLE Customer (Id INT PRIMARY KEY);\n"
        f"CREATE TABLE T (k INT PRIMARY KEY, {name} INT);"
    )
    schema, _ = from_ddl(sql)
    assert column(schema, "T", name).generator["strategy"] != "foreign_key"


# ---- 7. generated strings never exceed the declared length (F7) --------------------------------

SHORT_STRINGS = [
    "country_code CHAR(2)",
    "currency_code CHAR(3)",
    "code CHAR(3)",
    "region_code NCHAR(2)",
    "language_code VARCHAR(2)",
    "currency CHAR(3)",
    "product_code VARCHAR(5)",
    "kind_type VARCHAR(4)",
    "row_type VARCHAR(5)",
    "order_status VARCHAR(3)",
    "status VARCHAR(4)",
    "status CHAR(2)",
    "label CHAR(2)",
    "note VARCHAR(3)",
    "first_name VARCHAR(3)",
    "email VARCHAR(6)",
    "city VARCHAR(2)",
    "phone CHAR(4)",
    "zip CHAR(3)",
    "state CHAR(2)",
    "gender CHAR(2)",
    "tier CHAR(2)",
]


def longest(values: pa.ChunkedArray) -> int:
    return max((len(v) for v in values.to_pylist() if v is not None), default=0)


@pytest.mark.parametrize("smart", [True, False], ids=["smart", "plain"])
def test_short_fixed_length_columns_get_values_that_fit(smart: bool) -> None:
    cols = ", ".join(SHORT_STRINGS)
    schema, _ = from_ddl(
        f"CREATE TABLE t (id INT PRIMARY KEY, {cols});", smart=smart, scale="small:t=2000"
    )
    table = real_generate(schema, 7)["t"]
    for spec in SHORT_STRINGS:
        name, _, sql_type = spec.partition(" ")
        limit = int(re.search(r"\((\d+)\)", sql_type).group(1))  # type: ignore[union-attr]
        assert 0 < longest(table[name]) <= limit, spec


def test_a_two_character_code_is_two_characters() -> None:
    schema, _ = from_ddl("CREATE TABLE p (id INT PRIMARY KEY, country_code CHAR(2));")
    assert column(schema, "p", "country_code").generator == {
        "strategy": "pattern",
        "format": "{random:2}",
    }


def test_a_value_set_keeps_the_values_that_fit() -> None:
    schema, _ = from_ddl("CREATE TABLE p (id INT PRIMARY KEY, status VARCHAR(7));")
    assert set(column(schema, "p", "status").generator["values"]) == {"active", "pending"}


def test_a_value_set_none_of_which_fits_becomes_a_code_set() -> None:
    schema, _ = from_ddl("CREATE TABLE p (id INT PRIMARY KEY, status CHAR(2));")
    assert set(column(schema, "p", "status").generator["values"]) == {"A", "I", "P"}


def test_a_sequence_pattern_that_outgrows_the_column_is_random() -> None:
    sql = "CREATE TABLE p (id INT PRIMARY KEY, code VARCHAR(6));"
    schema, _ = from_ddl(sql, scale="small:p=999999")
    assert column(schema, "p", "code").generator["format"] == "{seq:6}"
    schema, _ = from_ddl(sql, scale="small:p=1000000")
    assert column(schema, "p", "code").generator["format"] == "{random:6}"


def test_columns_that_already_fit_are_unchanged() -> None:
    sql = "CREATE TABLE p (id INT PRIMARY KEY, code VARCHAR(10), status VARCHAR(20));"
    schema, _ = from_ddl(sql)
    assert column(schema, "p", "code").generator == {"strategy": "pattern", "format": "{seq:6}"}
    assert len(column(schema, "p", "status").generator["values"]) == 3


def fixture_inputs() -> dict[str, str]:
    root = Path(__file__).resolve().parents[2] / "benchmarks" / "vs_refengine" / "ddl_1to1"
    paths = sorted(root.glob("fixtures/*.sql")) + sorted(root.glob("extra/*.sql"))
    return {p.stem: p.read_text(encoding="utf-8") for p in paths}


@pytest.mark.parametrize("smart", [True, False], ids=["smart", "plain"])
@pytest.mark.parametrize("name", sorted(fixture_inputs()))
def test_every_ddl_fixture_generates_data_that_fits_and_whose_keys_exist(
    name: str, smart: bool
) -> None:
    schema, _ = from_ddl(fixture_inputs()[name], smart=smart)
    assert errors(schema) == []
    result = real_generate(schema, 11)
    for tname, table in schema.tables.items():
        data = result[tname]
        for cname, col in table.columns.items():
            if col.type == "string" and col.max_length:
                assert longest(data[cname]) <= col.max_length, (tname, cname)
    for rel in schema.relationships:
        for pcol, ccol in zip(rel.parent_columns, rel.child_columns, strict=True):
            parent_keys = set(result[rel.parent][pcol].to_pylist())
            child_keys = {v for v in result[rel.child][ccol].to_pylist() if v is not None}
            assert child_keys <= parent_keys, rel.name
