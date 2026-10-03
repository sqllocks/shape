"""ISS-gen #24 (generation side): the declared SQL type of a ``from-ddl`` column is kept.

Every test fails on the old code: ``DECIMAL(10,2)`` came out as ``double`` and a ``DATETIME``
carried microseconds."""

from __future__ import annotations

import decimal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from shape.generation.ddl import from_ddl
from shape.generation.engine import Engine
from shape.generation.schema import GenSchema

DDL = (
    "CREATE TABLE customer (customer_id INT PRIMARY KEY, email VARCHAR(100), "
    "created_at DATETIME, updated_at DATETIME2(0), seen_at DATETIME2(7));\n"
    "CREATE TABLE orders (order_id INT PRIMARY KEY, "
    "customer_id INT NOT NULL REFERENCES customer(customer_id), "
    "total DECIMAL(10,2), rate DECIMAL(9,4), whole NUMERIC(6), status VARCHAR(20));\n"
)


@pytest.fixture(scope="module")
def schema() -> GenSchema:
    return from_ddl(DDL)[0]


def test_decimal_columns_are_decimal128(schema: GenSchema) -> None:
    result = Engine(schema, seed=1).generate()
    fields = result.tables["orders"].schema
    assert fields.field("total").type == pa.decimal128(10, 2)
    assert fields.field("rate").type == pa.decimal128(9, 4)
    assert fields.field("whole").type == pa.decimal128(6, 0)
    totals = [v for v in result.tables["orders"]["total"].to_pylist() if v is not None]
    assert totals and all(isinstance(v, decimal.Decimal) for v in totals)
    assert all(v == round(v, 2) for v in totals)
    assert fields.field("order_id").type == pa.int64()  # keys are untouched


def test_datetime_has_no_more_digits_than_the_type(schema: GenSchema) -> None:
    customers = Engine(schema, seed=1).generate().tables["customer"]
    created = [v for v in customers["created_at"].to_pylist() if v is not None]
    updated = [v for v in customers["updated_at"].to_pylist() if v is not None]
    seen = [v for v in customers["seen_at"].to_pylist() if v is not None]
    assert created and all(v.microsecond % 1000 == 0 for v in created)  # DATETIME: milliseconds
    assert updated and all(v.microsecond == 0 for v in updated)  # DATETIME2(0): whole seconds
    assert any(v.microsecond % 1000 for v in seen)  # DATETIME2(7): microseconds are kept


def test_every_output_path_has_the_declared_type(schema: GenSchema, tmp_path: pa.Table) -> None:
    from shape.generation.output import write_engine

    engine = Engine(schema, seed=1)
    batch = next(iter(engine.iter_chunks("orders")))
    assert batch.schema.field("total").type == pa.decimal128(10, 2)
    paths = write_engine(Engine(schema, seed=1), "parquet", str(tmp_path))
    assert paths
    assert pq.read_schema(str(tmp_path / "orders.parquet")).field("total").type == pa.decimal128(
        10, 2
    )


def test_same_values_as_the_numbers_they_replace(schema: GenSchema) -> None:
    typed = Engine(schema, seed=3).generate().tables["orders"]["total"].to_pylist()
    untyped_schema = GenSchema.from_dict(schema.to_dict())
    for t in untyped_schema.tables.values():
        for c in t.columns.values():
            c.generator.pop("output_type", None)
    plain = Engine(untyped_schema, seed=3).generate().tables["orders"]["total"].to_pylist()
    assert [None if v is None else float(v) for v in typed] == plain


def test_post_passes_still_work_with_a_decimal_column() -> None:
    doc = {
        "schema_version": 1,
        "model": {"name": "t", "seed": 1},
        "tables": {
            "parent": {
                "name": "parent",
                "primary_key": ["id"],
                "columns": {
                    "id": {"name": "id", "type": "integer", "generator": {"strategy": "sequence"}},
                    "amount": {
                        "name": "amount",
                        "type": "decimal",
                        "precision": 12,
                        "scale": 2,
                        "generator": {
                            "strategy": "computed",
                            "rule": "sum_children",
                            "child_table": "child",
                            "child_column": "line",
                            "output_type": "decimal",
                        },
                    },
                },
            },
            "child": {
                "name": "child",
                "primary_key": ["id"],
                "columns": {
                    "id": {"name": "id", "type": "integer", "generator": {"strategy": "sequence"}},
                    "pid": {
                        "name": "pid",
                        "type": "integer",
                        "generator": {"strategy": "foreign_key", "ref": "parent.id"},
                    },
                    "line": {
                        "name": "line",
                        "type": "decimal",
                        "precision": 10,
                        "scale": 2,
                        "generator": {
                            "strategy": "distribution",
                            "distribution": "uniform",
                            "min": 1,
                            "max": 50,
                            "output_type": "decimal",
                        },
                    },
                },
            },
        },
        "relationships": [
            {
                "name": "r",
                "parent": "parent",
                "child": "child",
                "parent_columns": ["id"],
                "child_columns": ["pid"],
            }
        ],
        "generation": {"scale": "s", "scales": {"s": {"parent": 5, "child": 40}}},
    }
    result = Engine(GenSchema.from_dict(doc), seed=2).generate()
    parent, child = result.tables["parent"], result.tables["child"]
    assert parent.schema.field("amount").type == pa.decimal128(12, 2)
    sums: dict[int, decimal.Decimal] = {}
    for pid, line in zip(child["pid"].to_pylist(), child["line"].to_pylist(), strict=True):
        sums[pid] = sums.get(pid, decimal.Decimal(0)) + line
    for pid, amount in zip(parent["id"].to_pylist(), parent["amount"].to_pylist(), strict=True):
        assert amount == sums.get(pid, decimal.Decimal(0))


def test_a_value_that_does_not_fit_names_the_column() -> None:
    doc = from_ddl("CREATE TABLE t (id INT PRIMARY KEY, v DECIMAL(4,2));")[0].to_dict()
    doc["tables"]["t"]["columns"]["v"]["generator"] = {
        "strategy": "distribution",
        "distribution": "uniform",
        "min": 1000,
        "max": 2000,
        "output_type": "decimal",
    }
    with pytest.raises(ValueError, match=r"t\.v.*DECIMAL\(4,2\)"):
        Engine(GenSchema.from_dict(doc), seed=1).generate()
