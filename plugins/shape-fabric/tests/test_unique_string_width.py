"""UNIQUE string type validation happens before the lazy Fabric connection."""

import pyarrow as pa
import pytest
from shape_fabric import _tsql
from shape_fabric.sqldb import SqlDatabaseWriter, constraint_plan

from shape.errors import ShapeError


@pytest.mark.parametrize("warehouse,limit", [(False, 4000), (True, 8000)])
@pytest.mark.parametrize("width", [None, "oversize"])
def test_unique_string_width_refused_before_connection(warehouse, limit, width):
    def no_connection(*args, **kwargs):
        pytest.fail("UNIQUE width must be refused before connecting")

    writer = SqlDatabaseWriter(
        "Server=localhost;Database=shape", warehouse=warehouse, connect=no_connection
    )
    batch = pa.record_batch({"code": ["value"]})
    columns = {"code": {"max_length": limit + 1}} if width else {}
    with pytest.raises(ShapeError, match="column 'code'.*max_length"):
        writer.write_table("t", [batch], columns=columns, unique=[["code"]])


@pytest.mark.parametrize("warehouse,sql_type", [(False, "NVARCHAR(64)"), (True, "VARCHAR(64)")])
def test_declared_unique_does_not_make_nullable_column_primary(warehouse, sql_type):
    schema = pa.schema([("code", pa.string())])
    constraints = constraint_plan("dbo", "t", schema, warehouse, {"unique": [["code"]]})
    ddl = _tsql.create_table_sql(
        "dbo",
        "t",
        schema,
        warehouse=warehouse,
        columns={"code": {"max_length": 64, "nullable": True}},
        constraint_ddl=constraints,
    )
    assert f"[code] {sql_type} NULL" in ddl
    assert "NOT NULL" not in ddl and "PRIMARY KEY" not in ddl
    assert any(
        "UNIQUE" in clause for clause in (*constraints.definitions, *constraints.after_create)
    )
