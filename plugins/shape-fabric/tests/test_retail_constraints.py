"""W9-04 Wanted 5: the complete retail load through SQL Server's fake driver."""

import copy

import pyarrow as pa
import pytest
from shape_fabric import SqlServerSink, WriteError
from shape_fabric.testing import FakeSqlServer

from shape.generation.domains import load_domain
from shape.generation.engine import Engine
from shape.generation.output import TargetOptions, write_targets


def test_all_retail_rows_load_and_planted_check_violation_names_constraint(monkeypatch):
    server = FakeSqlServer()
    sink = SqlServerSink(connect=server.connect)
    monkeypatch.setattr("shape.io.targets.sink_for_target", lambda uri: ("sqlserver", sink))
    schema = copy.deepcopy(load_domain("retail").schema)
    for table in schema.tables.values():
        for column in table.columns.values():
            if column.type == "decimal":
                column.precision, column.scale = 18, 2
    engine = Engine(schema, scale="small", seed=1042)
    contract = {"tables": {"product": {"columns": {"unit_price": {"min": 0, "default": 1}}}}}
    options = TargetOptions(constraints="all", contract=contract)
    uri = "mssql://db.example/retail"
    expected = dict(engine.row_counts)
    assert len(expected) == 9 and sum(expected.values()) == 21750
    assert write_targets(engine, [uri], options, max_workers=1) == {uri: expected}
    assert {name: len(server.rows("dbo", name)) for name in expected} == expected
    creates = [i for i, sql in enumerate(server.statements) if sql.startswith("CREATE TABLE")]
    foreign_keys = [
        i
        for i, sql in enumerate(server.statements)
        if sql.startswith("ALTER TABLE") and "FOREIGN KEY" in sql
    ]
    inserts = [i for i, sql in enumerate(server.statements) if sql.startswith("INSERT INTO")]
    assert len(creates) == 9 and foreign_keys and inserts
    assert max(creates) < min(foreign_keys) <= max(foreign_keys) < min(inserts)
    product = Engine(schema, scale="small", seed=1042).generate().tables["product"].slice(0, 1)
    key = product.schema.get_field_index("product_id")
    cost = product.schema.get_field_index("cost")
    product = product.set_column(key, "product_id", pa.array([1000000], type=pa.int64()))
    product = product.set_column(cost, "cost", product.column("unit_price"))
    load_options = options.for_sink("sqlserver", schema, "product")
    load_options["write_mode"] = "append"
    with pytest.raises(WriteError, match="cost_less_than_price") as error:
        sink.write(uri, "product", product.to_batches(), **load_options)
    assert error.value.rows_committed == 0
    assert len(server.rows("dbo", "product")) == expected["product"]
