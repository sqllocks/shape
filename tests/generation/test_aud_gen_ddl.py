"""AUD-gen: reading SQL DDL (``from_ddl``)."""

from __future__ import annotations

import pytest

from shape.generation.ddl import from_ddl
from shape.generation.engine import Engine


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
