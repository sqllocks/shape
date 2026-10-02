"""SqlDatabaseWriter against a real SQL Server (the nightly ``sqlserver-e2e`` job's container).

    docker compose -f ci/emulators/docker-compose.yml up -d --wait mssql
    SHAPE_TEST_MSSQL='Driver={ODBC Driver 18 for SQL Server};Server=localhost,1433;UID=sa;PWD=...;\
Encrypt=yes;TrustServerCertificate=yes' \
pytest -m emulator plugins/shape-fabric/tests/test_sql_emulator.py

Each test works in its own schema and drops it afterwards. Nothing is skipped when the server is
missing: the nightly job must fail if it cannot reach it. This is where the statements the
recorded tapes pin meet a real engine (types, truncation, transactions, quoting).
"""

import datetime as dt
import os
import uuid
from decimal import Decimal

import pyarrow as pa
import pytest
from shape_fabric import SqlDatabaseWriter, WriteError, _tsql
from shape_fabric.testing import sample_batches

from shape.errors import ShapeError

pytestmark = pytest.mark.emulator

CS = os.environ.get(
    "SHAPE_TEST_MSSQL",
    "Driver={ODBC Driver 18 for SQL Server};Server=localhost,1433;UID=sa;PWD=Shape_Emulator_1;"
    "Encrypt=yes;TrustServerCertificate=yes",
)


@pytest.fixture
def schema():
    name = f"shape_w_{uuid.uuid4().hex[:10]}"
    yield name
    conn = _tsql.connect(CS)
    cursor = conn.cursor()
    for (table,) in cursor.execute(
        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = ?", name
    ).fetchall():
        cursor.execute(f"DROP TABLE {_tsql.qualified(name, table)}")
    cursor.execute(
        "IF EXISTS (SELECT 1 FROM sys.schemas WHERE name = ?) "
        f"EXEC('DROP SCHEMA {_tsql.ident(name)}')",
        name,
    )
    conn.commit()
    conn.close()


def rows_of(schema, table, order="id"):
    conn = _tsql.connect(CS)
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT * FROM {_tsql.qualified(schema, table)} ORDER BY {_tsql.ident(order)}"
        )  # nosec B608
        return cursor.fetchall()
    finally:
        conn.close()


def test_every_write_mode_against_a_real_server(schema):
    batches = sample_batches()
    with SqlDatabaseWriter(CS, schema_name=schema) as w:
        assert w.write_table("customer", batches, primary_key=["id"]) == 7
        rows = rows_of(schema, "customer")
        assert len(rows) == 7
        first = rows[0]
        assert first[3] == Decimal("0.00") and first[4] is None and first[5] is True
        assert first[6] == dt.date(2000, 1, 1)
        assert first[7] == dt.datetime(2026, 1, 1, 12, 0, 0, 123456)
        with pytest.raises(WriteError):  # the primary key rejects a repeat, and nothing is added
            w.write_table("customer", batches, write_mode="append")
        assert len(rows_of(schema, "customer")) == 7
        w.write_table("customer", [sample_batches()[1]], write_mode="truncate")
        assert len(rows_of(schema, "customer")) == 3
        w.write_table("customer", batches, write_mode="replace")
        assert len(rows_of(schema, "customer")) == 7
        with pytest.raises(ShapeError, match="already exists"):
            w.write_table("customer", batches)


def test_strings_are_not_cut_when_the_first_row_is_the_shortest(schema):
    values = ["a", "b" * 50, "é" * 20, "O'Brien; DROP TABLE x;--", "x" * 4001]
    batch = pa.RecordBatch.from_arrays([pa.array(range(5)), pa.array(values)], names=["id", "s"])
    with SqlDatabaseWriter(CS, schema_name=schema) as w:
        w.write_table("t", [batch], batch_size=5)
    assert [r[1] for r in rows_of(schema, "t")] == values


def test_a_failed_batch_rolls_back_the_table_it_created(schema):
    ok = pa.RecordBatch.from_arrays([pa.array(["ab"])], names=["code"])
    too_long = pa.RecordBatch.from_arrays([pa.array(["abcdef"])], names=["code"])
    with SqlDatabaseWriter(CS, schema_name=schema) as w:
        with pytest.raises(WriteError):
            w.write_table("t", [ok, too_long], columns={"code": {"max_length": 3}})
        assert not w.db.table_exists(schema, "t")  # dropped again


def test_awkward_names_cannot_break_out_of_their_quotes(schema):
    table = "t]; DROP TABLE users; --"
    batch = pa.RecordBatch.from_arrays([pa.array([1]), pa.array(["x"])], names=["a]]b", "it's"])
    with SqlDatabaseWriter(CS, schema_name=schema) as w:
        assert w.write_table(table, [batch]) == 1
        assert w.db.table_exists(schema, table)
    assert rows_of(schema, table, order="a]]b") == [(1, "x")]
