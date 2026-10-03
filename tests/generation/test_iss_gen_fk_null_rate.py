"""ISS-gen: a nullable foreign key from ``from-ddl`` gets its 0.15 null rate on the column.

It was written into the generator, where the engine never reads it, so the declared rate did
nothing: the FK column had no nulls."""

from __future__ import annotations

import pytest

from shape.generation.ddl import from_ddl
from shape.generation.engine import Engine

DDL = (
    "CREATE TABLE customer (customer_id INT PRIMARY KEY, name VARCHAR(40));\n"
    "CREATE TABLE orders (order_id INT PRIMARY KEY, "
    "customer_id INT REFERENCES customer(customer_id), "
    "region_id INT NOT NULL REFERENCES customer(customer_id));\n"
)


def test_the_null_rate_is_on_the_column_not_the_generator() -> None:
    cols = from_ddl(DDL)[0].tables["orders"].columns
    assert cols["customer_id"].null_rate == 0.15
    assert "null_rate" not in cols["customer_id"].generator
    assert cols["region_id"].null_rate == 0.0


def test_the_generated_foreign_key_null_rate_matches() -> None:
    schema = from_ddl(DDL)[0]
    result = Engine(schema, seed=7, row_counts={"orders": 20_000, "customer": 500}).generate()
    orders = result.tables["orders"]
    rate = orders["customer_id"].null_count / orders.num_rows
    assert rate == pytest.approx(0.15, abs=0.02)
    assert orders["region_id"].null_count == 0
    parents = set(result.tables["customer"]["customer_id"].to_pylist())
    assert set(v for v in orders["customer_id"].to_pylist() if v is not None) <= parents


def test_validate_no_longer_warns_about_the_fk_null_rate() -> None:
    schema = from_ddl(DDL)[0]
    assert not [i for i in schema.validate() if "null_rate" in i.message]
