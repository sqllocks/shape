"""AUD-gen: reading SQL DDL (``from_ddl``)."""

from __future__ import annotations

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
