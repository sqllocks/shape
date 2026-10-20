"""Write-constraint DDL, preconnection validation and exact retained-row errors."""

import pyarrow as pa
import pytest
from shape_fabric import SqlServerSink, WriteError
from shape_fabric.sqldb import SqlDatabaseWriter
from shape_fabric.testing import FakeSqlServer

from shape.errors import ShapeError


def batch():
    return pa.RecordBatch.from_pydict({"id": [1], "parent_id": [1], "amount": [3]})


def declarations():
    return {
        "unique": [["id", "parent_id"]],
        "defaults": {"amount": 2},
        "checks": [
            {
                "name": "ck_amount",
                "expression": {"op": ">=", "left": {"column": "amount"}, "right": {"literal": 0}},
            }
        ],
    }


def test_sqlserver_new_table_ddl_and_fk_phase():
    server = FakeSqlServer()
    sink = SqlServerSink(connect=server.connect)
    schema = batch().schema
    sink.prepare_constraints(
        "mssql://db.example/app",
        {
            "parent": {"schema": schema, "primary_key": ["id"]},
            "child": {
                "schema": schema,
                "primary_key": ["id"],
                **declarations(),
                "foreign_keys": [
                    {
                        "name": "fk_child_parent",
                        "columns": ["parent_id"],
                        "ref_table": "parent",
                        "ref_columns": ["id"],
                    }
                ],
            },
        },
    )
    statements = server.statements
    creates = [i for i, sql in enumerate(statements) if sql.startswith("CREATE TABLE")]
    fk = next(i for i, sql in enumerate(statements) if "ADD CONSTRAINT [fk_child_parent]" in sql)
    assert len(creates) == 2 and max(creates) < fk
    assert "UNIQUE" in statements[creates[-1]] and "CHECK" in statements[creates[-1]]
    assert "DEFAULT 2" in statements[creates[-1]]
    assert server.rows("dbo", "parent") == server.rows("dbo", "child") == []


def test_sqlserver_bad_constraints_refused_before_connection():
    server = FakeSqlServer()
    with SqlDatabaseWriter("Server=x;Database=d", connect=server.connect) as writer:
        with pytest.raises(ShapeError, match="structured"):
            writer.write_table("child", [batch()], checks=[{"name": "bad", "expression": "id > 0"}])
    assert server.connections == 0 and server.statements == []


def test_sqlserver_partial_constraint_failure_reports_exact_commits():
    server = FakeSqlServer()
    inserts = 0

    def fail(sql, params):
        nonlocal inserts
        if sql.startswith("INSERT INTO"):
            inserts += 1
            if inserts == 2:
                raise RuntimeError('constraint "ck_amount" violated at row 2')

    server.fail = fail
    with SqlDatabaseWriter("Server=x;Database=d", connect=server.connect) as writer:
        with pytest.raises(WriteError, match="ck_amount.*row 2") as error:
            writer.write_table(
                "child", [pa.RecordBatch.from_pydict({"id": [1, 2]})], batch_size=1, commit_rows=1
            )
    assert error.value.rows_committed == 1
    assert server.rows("dbo", "child") == [(1,)]


def test_warehouse_informational_keys_and_explicit_unsupported_notes(caplog):
    from shape_fabric.testing import MemoryFS
    from shape_fabric.warehouse import WarehouseWriter

    server = FakeSqlServer(warehouse=True)
    with WarehouseWriter(
        "Server=x;Database=d",
        "onelake://ws/lh/Files/stage",
        connect=server.connect,
        filesystem=MemoryFS(),
    ) as writer:
        assert writer.write_table("child", [], schema=batch().schema, **declarations()) == 0
    ddl = "\n".join(server.statements)
    assert "UNIQUE NONCLUSTERED" in ddl and "NOT ENFORCED" in ddl
    assert " CHECK " not in ddl and " DEFAULT " not in ddl
    assert sum("CHECK" in record.message for record in caplog.records) == 1
    assert sum("DEFAULT" in record.message for record in caplog.records) == 1


def test_constraint_reference_prefix_matches_physical_tables():
    server = FakeSqlServer()
    sink = SqlServerSink(connect=server.connect)
    sink.prepare_constraints(
        "mssql://db.example/app",
        {
            "parent": {"schema": batch().schema, "primary_key": ["id"], "table_prefix": "run_"},
            "child": {
                "schema": batch().schema,
                "table_prefix": "run_",
                "foreign_keys": [
                    {
                        "name": "fk_child_parent",
                        "columns": ["parent_id"],
                        "ref_table": "parent",
                        "ref_columns": ["id"],
                    }
                ],
            },
        },
    )
    foreign = next(sql for sql in server.statements if "ADD CONSTRAINT [fk_child_parent]" in sql)
    assert "ALTER TABLE [dbo].[run_child]" in foreign
    assert "REFERENCES [dbo].[run_parent]" in foreign
