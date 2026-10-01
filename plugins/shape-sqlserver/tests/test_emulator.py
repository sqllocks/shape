"""End to end against a real SQL Server 2022 (the nightly job's container).

    docker compose -f ci/emulators/docker-compose.yml up -d --wait mssql
    pytest -m emulator plugins/shape-sqlserver/tests

Needs pyodbc and the Microsoft ODBC Driver 18. ``SHAPE_TEST_MSSQL`` overrides the connection
string of the compose file's server. Nothing here is skipped when the server is missing: the
nightly job must fail if it cannot reach it.
"""

import contextlib
import json
import os
import subprocess
import sys
import time

import pyarrow as pa
import pytest
from shape_sqlserver import Credentials, SqlServerSource, connect, profile_database
from shape_sqlserver.testing import ddl, insert_rows, scenario

import shape

pytestmark = pytest.mark.emulator

DEFAULT = (
    "Driver={ODBC Driver 18 for SQL Server};Server=localhost,1433;UID=sa;PWD=Shape_Emulator_1;"
    "Encrypt=yes;TrustServerCertificate=yes"
)
DATABASE = "shape_e2e"


def _base() -> str:
    return os.environ.get("SHAPE_TEST_MSSQL", DEFAULT)


@pytest.fixture(scope="module")
def conn_str():
    import pyodbc

    deadline = time.monotonic() + 120
    while True:
        try:
            admin = pyodbc.connect(_base(), autocommit=True, timeout=10)
            break
        except pyodbc.Error:
            if time.monotonic() > deadline:
                raise
            time.sleep(3)
    cur = admin.cursor()
    cur.execute(f"DROP DATABASE IF EXISTS [{DATABASE}]")
    cur.execute(f"CREATE DATABASE [{DATABASE}]")
    admin.close()
    text = f"{_base()};Database={DATABASE}"
    with contextlib.closing(pyodbc.connect(text, autocommit=True)) as conn:
        cur = conn.cursor()
        retail = scenario("retail")
        for stmt in ddl(retail):
            cur.execute(stmt)
        for table in retail.tables:
            insert_rows(cur, table)
        cur.execute("CREATE SCHEMA wh")
        warehouse = scenario("warehouse")
        for stmt in ddl(warehouse, "wh"):
            cur.execute(stmt)
        for table in warehouse.tables:
            insert_rows(cur, table, "wh")
        cur.execute("CREATE TABLE dbo.stamps (id int NOT NULL PRIMARY KEY, ts datetimeoffset(7))")
        cur.execute(
            "INSERT INTO dbo.stamps VALUES (1, '2024-03-05 10:30:15.1234567 +02:00'), "
            "(2, '2024-03-05 10:30:15.1234567 -05:00'), (3, NULL)"
        )
    return text


def _profile(conn_str, **kw):
    return profile_database(conn_str, credentials=Credentials("sql"), **kw).to_dict()


def test_catalog_walk_matches_the_loaded_schema(conn_str):
    prof = _profile(conn_str)
    local = profile_database(connection=scenario("retail")).to_dict()
    assert {t for t in prof["tables"]} == set(local["tables"]) | {"stamps"}
    for name, table in local["tables"].items():
        real = prof["tables"][name]
        assert real["row_count"] == table["row_count"], name
        assert real["primary_key"] == table["primary_key"], name
        assert real["detected_fks"] == table["detected_fks"], name
        assert list(real["columns"]) == list(table["columns"]), name
    assert sorted(r["name"] for r in prof["relationships"]) == sorted(
        r["name"] for r in local["relationships"]
    )


def test_sampled_statistics_match_the_in_memory_server(conn_str):
    prof = _profile(conn_str)
    local = profile_database(connection=scenario("retail")).to_dict()
    for name, table in local["tables"].items():
        for col, want in table["columns"].items():
            got = prof["tables"][name]["columns"][col]
            for field in (
                "dtype",
                "null_count",
                "null_rate",
                "cardinality",
                "cardinality_ratio",
                "is_unique",
                "is_enum",
                "is_primary_key",
                "is_foreign_key",
                "fk_ref_table",
            ):
                assert got[field] == want[field], f"{name}.{col}.{field}"
            if col != "rating":  # a real is a float32 on the server
                assert got["min_value"] == want["min_value"], f"{name}.{col}"
                assert got["mean"] == pytest.approx(want["mean"], rel=1e-9) or want["mean"] is None


def test_datetimeoffset_is_profiled_not_dropped(conn_str):
    stamps = _profile(conn_str, tables=["stamps"])["tables"]["stamps"]["columns"]["ts"]
    assert stamps["dtype"] == "datetime"
    assert stamps["null_count"] == 1
    assert stamps["cardinality"] == 2  # the same wall-clock time at two offsets: two instants


def test_keyless_schema_gets_guessed_keys_and_name_links(conn_str):
    prof = _profile(conn_str, schema="wh")
    assert prof["tables"]["dimcustomer"]["primary_key"] == ["customer_key"]
    assert prof["tables"]["factsales"]["row_count"] == 900


def test_sample_rows_zero_reads_no_data(conn_str):
    prof = _profile(conn_str, schema="wh", sample_rows=0)
    assert prof["tables"]["dimcustomer"]["columns"]["region"]["cardinality"] == 0
    assert prof["tables"]["dimcustomer"]["row_count"] == 120


def test_source_reads_a_table_in_key_order(conn_str):
    src = SqlServerSource()
    uri = "mssql:///?table=orders&schema=dbo"
    table = pa.Table.from_batches(
        list(src.read(uri, connection_string=conn_str, auth="sql", batch_size=1000))
    )
    rows = sorted(next(t for t in scenario("retail").tables if t.name == "orders").rows)
    assert table.num_rows == len(rows) == 4000
    assert table.column("order_id").to_pylist() == [r[0] for r in rows]
    assert table.column("total").to_pylist() == [r[4] for r in rows]
    assert table.schema.equals(src.schema(uri, connection_string=conn_str, auth="sql"))
    assert pa.types.is_decimal(table.schema.field("total").type)


def test_source_reads_datetimeoffset_as_utc(conn_str):
    src = SqlServerSource()
    table = pa.Table.from_batches(
        list(src.read("mssql:///?table=stamps", connection_string=conn_str, auth="sql"))
    )
    values = table.column("ts").to_pylist()
    assert str(values[0].isoformat()) == "2024-03-05T08:30:15.123456+00:00"
    assert str(values[1].isoformat()) == "2024-03-05T15:30:15.123456+00:00"
    assert values[2] is None


def test_hostile_object_names_are_quoted(conn_str):
    import pyodbc

    with contextlib.closing(pyodbc.connect(conn_str, autocommit=True)) as conn:
        cur = conn.cursor()
        cur.execute("CREATE TABLE dbo.[a]]b; --] (v int)")
        cur.execute("INSERT INTO dbo.[a]]b; --] VALUES (1), (2)")
    prof = _profile(conn_str, tables=["a]b; --"])
    assert prof["tables"]["a]b; --"]["columns"]["v"]["cardinality"] == 2


def test_connect_returns_a_usable_connection(conn_str):
    conn = connect(conn_str, Credentials("sql"))
    try:
        assert conn.cursor().execute("SELECT 1").fetchone()[0] == 1
    finally:
        conn.close()


def test_profile_db_command_end_to_end(conn_str, tmp_path):
    out = tmp_path / "db.shape"
    env = dict(os.environ, SHAPE_SQLSERVER_CONNECTION_STRING=conn_str)
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "shape.cli.main",
            "profile-db",
            "--auth",
            "sql",
            "--schema",
            "dbo",
            "--tables",
            "orders,customer",
            "-o",
            str(out),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["tables"] == 2
    assert sorted(shape.load(out).tables) == ["customer", "orders"]
