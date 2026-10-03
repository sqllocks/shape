"""AUD-gen: reading SQL DDL (``from_ddl``)."""

from __future__ import annotations

import pytest

from shape.generation.ddl import from_ddl
from shape.generation.engine import Engine, calculate_row_counts


def test_declared_foreign_keys_match_tables_and_columns_whatever_their_case():
    # 172: REFERENCES customer(id) to a table declared as Customer (Id) kept ref customer.id,
    # made no relationship and failed: FK references non-existent table 'customer'.
    schema, _ = from_ddl(
        "CREATE TABLE Customer (Id INT PRIMARY KEY, email VARCHAR(50));"
        "CREATE TABLE Orders (Id INT PRIMARY KEY, CustId INT NOT NULL REFERENCES customer(id))"
    )
    assert schema.tables["Orders"].columns["CustId"].generator["ref"] == "Customer.Id"
    assert [(r.parent, r.parent_columns) for r in schema.relationships] == [("Customer", ["Id"])]
    res = Engine(schema, row_counts={"Customer": 10, "Orders": 50}).generate()
    keys = set(res.tables["Customer"]["Id"].to_pylist())
    assert set(res.tables["Orders"]["CustId"].to_pylist()) <= keys


def test_bracket_quoted_types_are_read_as_their_type():
    # 173: SSMS "Script Table as" writes [int] and [decimal](18, 2); both became string + faker.
    schema, _ = from_ddl(
        "CREATE TABLE [dbo].[Sales]([SaleId] [int] IDENTITY(1,1) NOT NULL, "
        "[BuyerKey] [int] NOT NULL, [Amount] [decimal](18, 2) NOT NULL, "
        "[Note] [nvarchar](40) NULL)",
        smart=False,
    )
    cols = schema.tables["Sales"].columns
    assert cols["BuyerKey"].type == "integer"
    assert (cols["Amount"].type, cols["Amount"].precision, cols["Amount"].scale) == (
        "decimal",
        18,
        2,
    )
    assert (cols["Note"].type, cols["Note"].max_length, cols["Note"].nullable) == (
        "string",
        40,
        True,
    )


_PARENT_CHILD = (
    "CREATE TABLE [dbo].[Customers] ([CustomerKey] [int] NOT NULL PRIMARY KEY, [Name] "
    "[nvarchar](40) NULL);"
    "CREATE TABLE [dbo].[Sales] ([SaleId] [int] NOT NULL PRIMARY KEY, [BuyerKey] [int] NOT NULL);"
)


@pytest.mark.parametrize(
    "fk",
    [
        "ALTER TABLE [dbo].[Sales]  WITH CHECK ADD  CONSTRAINT [FK_x] FOREIGN KEY([BuyerKey]) "
        "REFERENCES [dbo].[Customers] ([CustomerKey])",
        "ALTER TABLE [dbo].[Sales] WITH NOCHECK ADD CONSTRAINT [FK_x] FOREIGN KEY([BuyerKey]) "
        "REFERENCES [dbo].[Customers] ([CustomerKey])",
        "ALTER TABLE Sales ADD FOREIGN KEY (BuyerKey) REFERENCES Customers(CustomerKey)",
        "ALTER TABLE Sales ADD CONSTRAINT f FOREIGN KEY (BuyerKey) REFERENCES Customers",
    ],
)
def test_alter_table_foreign_keys_in_every_common_form(fk):
    # 174: the SQL Server script form (WITH CHECK ADD), an unnamed constraint and a reference
    # to the parent's key alone gave no foreign key.
    schema, _ = from_ddl(_PARENT_CHILD + fk, smart=False)
    assert schema.tables["Sales"].columns["BuyerKey"].generator["ref"] == "Customers.CustomerKey"
    assert [(r.parent, r.child) for r in schema.relationships] == [("Customers", "Sales")]


def test_a_table_level_foreign_key_to_the_parents_key():
    # 174: FOREIGN KEY (col) REFERENCES parent, with no column list, was dropped.
    schema, _ = from_ddl(
        "CREATE TABLE c (cid INT PRIMARY KEY);"
        "CREATE TABLE o (id INT PRIMARY KEY, buyer INT, FOREIGN KEY (buyer) REFERENCES c)",
        smart=False,
    )
    assert schema.tables["o"].columns["buyer"].generator["ref"] == "c.cid"


@pytest.mark.parametrize("smart", [True, False])
@pytest.mark.parametrize(
    "child",
    [
        "CREATE TABLE customer_profile (customer_id INT PRIMARY KEY, bio VARCHAR(200))",
        "CREATE TABLE customer_profile (cust INT PRIMARY KEY REFERENCES customer(id), "
        "bio VARCHAR(200))",
    ],
)
def test_a_primary_key_that_is_a_foreign_key_is_unique(child, smart):
    # 175: the key got a skewed foreign_key generator: 500 rows, 85 distinct keys.
    schema, _ = from_ddl(
        "CREATE TABLE customer (id INT PRIMARY KEY, email VARCHAR(50));" + child, smart=smart
    )
    res = Engine(schema).generate()
    key = res.tables["customer_profile"].column(0)
    parents = set(res.tables["customer"]["id"].to_pylist())
    assert key.null_count == 0
    assert len(set(key.to_pylist())) == len(key) > 0
    assert set(key.to_pylist()) <= parents


@pytest.mark.parametrize("smart", [True, False])
def test_composite_primary_key_columns_are_never_null(smart):
    # 175: without NOT NULL the key columns got null_rate 0.15 (FK-04). They stay declared
    # nullable, as the baseline reads them (ddl_1to1), but are never generated null.
    schema, _ = from_ddl(
        "CREATE TABLE orders (id INT PRIMARY KEY); CREATE TABLE product (id INT PRIMARY KEY);"
        "CREATE TABLE order_product (order_id INT REFERENCES orders(id), "
        "product_id INT REFERENCES product(id), qty INT, PRIMARY KEY (order_id, product_id))",
        smart=smart,
    )
    table = schema.tables["order_product"]
    for name in ("order_id", "product_id"):
        assert table.columns[name].null_rate == 0
    res = Engine(schema).generate()
    assert res.tables["order_product"]["order_id"].null_count == 0
    assert res.tables["order_product"]["product_id"].null_count == 0


def test_a_scale_override_sets_the_row_count_over_smart_inference():
    # 176: -s medium:order=7777 wrote scales.medium.order, but smart inference's derived count
    # (per_parent) took precedence: order got 25000 rows.
    schema, _ = from_ddl(
        "CREATE TABLE customer (id INT PRIMARY KEY, name VARCHAR(40));"
        "CREATE TABLE orders (id INT PRIMARY KEY, customer_id INT REFERENCES customer(id), "
        "total DECIMAL(10,2))",
        scale="medium:customer=5000,orders=7777",
    )
    assert calculate_row_counts(schema) == {"customer": 5000, "orders": 7777}


@pytest.mark.parametrize("gap", ["  ", "\n    ", "\t"])
def test_not_null_with_any_whitespace(gap):
    # 198: "NOT  NULL" made the base type "int not" (string + faker) and the column nullable.
    schema, _ = from_ddl(f"CREATE TABLE t (id INT PRIMARY KEY, a INT NOT{gap}NULL)", smart=False)
    a = schema.tables["t"].columns["a"]
    assert (a.type, a.nullable) == ("integer", False)


@pytest.mark.parametrize(
    ("body", "columns"),
    [
        (
            "id INT PRIMARY KEY, sep VARCHAR(5) DEFAULT ')', name VARCHAR(50) NOT NULL",
            ["id", "sep", "name"],
        ),
        (
            "id INT PRIMARY KEY, sep VARCHAR(5) DEFAULT 'a, b c', name VARCHAR(50)",
            ["id", "sep", "name"],
        ),
        ("id INT PRIMARY KEY, [a--b] INT, c INT", ["id", "a--b", "c"]),
        ('id INT PRIMARY KEY, "x/*y" INT, z INT, "w*/" INT', ["id", "x/*y", "z", "w*/"]),
        (
            "id INT PRIMARY KEY, [O'Neil] INT, -- a comment with (paren\n c INT",
            ["id", "O'Neil", "c"],
        ),
    ],
)
def test_literals_and_quoted_names_do_not_split_or_end_a_table(body, columns):
    # 197: a ')' or ',' in a DEFAULT literal, and comment markers or an apostrophe in a quoted
    # name, lost columns or the whole table, or added a phantom column.
    schema, _ = from_ddl(f"CREATE TABLE t ({body})", smart=False)
    assert list(schema.tables["t"].columns) == columns


@pytest.mark.parametrize(
    ("default", "nullable", "key", "type_"),
    [
        ("'NOT NULL'", True, ["id"], "string"),
        ("'PRIMARY KEY'", True, ["id"], "string"),
        ("'identity'", True, ["id"], "string"),
    ],
)
def test_keywords_inside_a_default_literal_are_text(default, nullable, key, type_):
    # 197: DEFAULT 'NOT NULL' made the column not nullable, 'PRIMARY KEY' the key, 'identity'
    # an integer sequence.
    schema, _ = from_ddl(
        f"CREATE TABLE t (id INT PRIMARY KEY, note VARCHAR(30) DEFAULT {default})", smart=False
    )
    note = schema.tables["t"].columns["note"]
    assert (note.nullable, schema.tables["t"].primary_key, note.type) == (nullable, key, type_)
    assert note.generator["strategy"] != "sequence"


def test_unclosed_headers_are_read_in_linear_time():
    # 204: each unclosed "CREATE TABLE t (" scanned to the end of the input: 64 KB took 10 s,
    # about 40 minutes at the 1 MB limit.
    import time

    from shape.generation.ddl import DdlParser

    started = time.perf_counter()
    DdlParser().parse_string("CREATE TABLE t (" * 4000)
    assert time.perf_counter() - started < 2.0


@pytest.mark.parametrize(
    ("declared", "type_"),
    [
        ("INT UNSIGNED", "integer"),
        ("BIGINT UNSIGNED ZEROFILL", "integer"),
        ("DOUBLE", "float"),
        ("TIMESTAMP WITH TIME ZONE", "timestamp"),
        ("timestamp without time zone", "timestamp"),
        ("TIMESTAMP(3) WITH TIME ZONE", "timestamp"),
    ],
)
def test_mysql_and_postgres_types(declared, type_):
    # 199: these fell through to string + faker text.
    schema, _ = from_ddl(f"CREATE TABLE t (id INT PRIMARY KEY, a {declared} NULL)", smart=False)
    a = schema.tables["t"].columns["a"]
    assert a.type == type_
    assert a.generator["strategy"] != "faker"


def test_a_mysql_enum_draws_its_values():
    # 199: ENUM('a','b') fell through to string + faker text.
    schema, _ = from_ddl(
        "CREATE TABLE t (id INT PRIMARY KEY, size ENUM('small','large','x''l') NOT NULL)",
        smart=False,
    )
    size = schema.tables["t"].columns["size"]
    assert size.generator["strategy"] == "weighted_enum"
    assert sorted(size.generator["values"]) == ["large", "small", "x'l"]


def test_a_mysql_key_line_is_an_index_not_a_column():
    # 199: KEY idx_a (a) became a string column named KEY.
    schema, _ = from_ddl(
        "CREATE TABLE t (id INT PRIMARY KEY, a INT, `key` VARCHAR(10), KEY idx_a (a), "
        "UNIQUE KEY uq (a), FULLTEXT KEY ft (`key`))",
        smart=False,
    )
    assert list(schema.tables["t"].columns) == ["id", "a", "key"]


def test_a_foreign_key_to_a_table_not_in_the_file_is_a_plain_column():
    # 203: the relationship was dropped but the column kept foreign_key -> ghost.id, so the
    # schema failed validation: FK references non-existent table 'ghost'.
    schema, _ = from_ddl(
        "CREATE TABLE t (id INT PRIMARY KEY, x INT REFERENCES ghost(id), "
        "y INT, FOREIGN KEY (y) REFERENCES ghost)",
        smart=False,
    )
    for name in ("x", "y"):
        assert schema.tables["t"].columns[name].generator["strategy"] != "foreign_key"
    assert not [i for i in schema.validate() if i.level == "error"]


def test_one_foreign_key_declared_several_ways_is_one_relationship():
    # 203: inline REFERENCES + table-level FOREIGN KEY + ALTER gave three fk_o_c_id.
    schema, _ = from_ddl(
        "CREATE TABLE c (id INT PRIMARY KEY);"
        "CREATE TABLE o (id INT PRIMARY KEY, c_id INT REFERENCES c(id), "
        "FOREIGN KEY (c_id) REFERENCES c(id));"
        "ALTER TABLE o ADD CONSTRAINT f FOREIGN KEY (c_id) REFERENCES c(id)",
        smart=False,
    )
    assert [r.name for r in schema.relationships] == ["fk_o_c_id"]


def test_same_named_tables_in_two_schemas_are_an_error():
    # 200: crm.customer silently replaced sales.customer (one table with columns id, b).
    from shape.generation.ddl import DdlError

    with pytest.raises(DdlError, match=r"sales\.customer.*crm\.customer"):
        from_ddl(
            "CREATE TABLE sales.customer (id INT PRIMARY KEY, a INT);"
            "CREATE TABLE crm.customer (id INT PRIMARY KEY, b INT)",
            smart=False,
        )


def test_a_quoted_table_name_with_a_dot_is_an_error_not_a_rename():
    # 200: [dbo].[my.table] became the table "table". A generation schema names a column
    # "table.column", so a dot cannot be part of a table name: it is an error that says so.
    from shape.generation.ddl import DdlError

    with pytest.raises(DdlError, match=r"'my\.table'"):
        from_ddl("CREATE TABLE [dbo].[my.table] (id INT PRIMARY KEY)", smart=False)


@pytest.mark.parametrize("smart", [False, True])
def test_declared_logical_types_are_kept_in_the_data(smart):
    # 202: BIT came out as double 1.0/0.0, BOOLEAN as 'true'/'false' strings, TIME as full
    # timestamps and DATE as timestamps with a time of day.
    import datetime as dt

    import pyarrow as pa  # type: ignore[import-untyped]

    schema, _ = from_ddl(
        "CREATE TABLE t (id INT PRIMARY KEY, tm TIME, flag BIT, b BOOLEAN, d DATE)", smart=smart
    )
    t = Engine(schema, row_counts={"t": 200}).generate().tables["t"]
    assert t.schema.field("flag").type == pa.bool_()
    assert t.schema.field("b").type == pa.bool_()
    assert t.schema.field("d").type == pa.date32()
    assert t.schema.field("tm").type == pa.time64("us")
    assert all(isinstance(v, dt.time) for v in t["tm"].to_pylist() if v is not None)


def test_transaction_dates_stay_in_the_models_date_range():
    # 195: the seasonal transaction-date generator had no range_ref: values from 2022-01-01.
    import datetime as dt

    schema, _ = from_ddl(
        "CREATE TABLE customer (id INT PRIMARY KEY, name VARCHAR(40));"
        "CREATE TABLE orders (id INT PRIMARY KEY, customer_id INT REFERENCES customer(id), "
        "order_date DATE NOT NULL)"
    )
    span = schema.model.date_range
    dates = Engine(schema).generate().tables["orders"]["order_date"].to_pylist()
    low, high = dt.date.fromisoformat(span["start"]), dt.date.fromisoformat(span["end"])
    assert all(low <= d <= high for d in dates), (min(dates), max(dates), span)
