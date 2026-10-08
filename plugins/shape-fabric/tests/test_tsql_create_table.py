"""CREATE TABLE: a table needs a column; a long table name still gets a key (#456)."""

from __future__ import annotations

import pyarrow as pa
import pytest
from shape_fabric._tsql import create_table_sql

from shape.errors import ShapeError


@pytest.mark.parametrize("warehouse", [False, True])
def test_a_table_without_columns_is_a_shape_error(warehouse: bool) -> None:
    with pytest.raises(ShapeError, match="column"):
        create_table_sql("dbo", "t", pa.schema([]), warehouse=warehouse)


@pytest.mark.parametrize("length", [125, 126, 128])
def test_a_long_table_name_gets_a_key_name_within_128_characters(length: int) -> None:
    table = "x" * length
    sql = create_table_sql(
        "dbo", table, pa.schema([pa.field("id", pa.int64())]), warehouse=False, primary_key=["id"]
    )
    name = sql.split("CONSTRAINT [", 1)[1].split("]", 1)[0]
    assert len(name) <= 128 and name.startswith("PK_x")


def test_different_long_names_get_different_key_names() -> None:
    schema = pa.schema([pa.field("id", pa.int64())])
    names = set()
    for last in "ab":
        sql = create_table_sql("dbo", "x" * 127 + last, schema, warehouse=False, primary_key=["id"])
        names.add(sql.split("CONSTRAINT [", 1)[1].split("]", 1)[0])
    assert len(names) == 2


def test_a_short_name_keeps_its_key_name() -> None:
    sql = create_table_sql(
        "dbo",
        "orders",
        pa.schema([pa.field("id", pa.int64())]),
        warehouse=False,
        primary_key=["id"],
    )
    assert "CONSTRAINT [PK_orders] PRIMARY KEY ([id])" in sql
