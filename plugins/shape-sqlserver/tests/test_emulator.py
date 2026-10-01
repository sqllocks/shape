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
        for schema_name, scenario_name in (
            ("idn", "id_named"),
            ("nom", "name_only"),
            ("mix", "mixed_keys"),
            ("kn", "key_names"),
        ):
            keyless = scenario(scenario_name)
            cur.execute(f"CREATE SCHEMA {schema_name}")
            for stmt in ddl(keyless, schema_name):
                cur.execute(stmt)
            for table in keyless.tables:
                insert_rows(cur, table, schema_name)
        cur.execute("CREATE SCHEMA hp")  # a heap (no key) with a column CHECKSUM cannot hash
        cur.execute("CREATE TABLE hp.events (a int, body xml, b varchar(10))")
        cur.executemany(
            "INSERT INTO hp.events VALUES (?, '<x/>', ?)",
            [(i, f"v{i % 9}") for i in range(3000)],
        )
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
        whole = table["row_count"] <= 1000  # larger tables are sampled with the server's CHECKSUM
        assert prof["tables"][name]["sample_method"] == ("all rows" if whole else "checksum spread")
        for col, want in table["columns"].items():
            got = prof["tables"][name]["columns"][col]
            for field in ("dtype", "is_primary_key", "is_foreign_key", "fk_ref_table"):
                assert got[field] == want[field], f"{name}.{col}.{field}"
            if not whole:
                continue
            for field in (
                "null_count",
                "null_rate",
                "cardinality",
                "cardinality_ratio",
                "is_unique",
                "is_enum",
            ):
                assert got[field] == want[field], f"{name}.{col}.{field}"
            if col != "rating":  # a real is a float32 on the server
                assert got["min_value"] == want["min_value"], f"{name}.{col}"
                assert got["mean"] == pytest.approx(want["mean"], rel=1e-9) or want["mean"] is None


def test_ratios_divide_by_the_rows_sampled_on_a_real_server(conn_str):
    prof = _profile(conn_str)
    customer = prof["tables"]["customer"]
    assert customer["row_count"] == 2500 and customer["sampled_rows"] == 1000
    assert prof["sampling"]["requested_rows"] == 1000
    ids = customer["columns"]["customer_id"]
    assert ids["cardinality"] == 1000
    assert ids["cardinality_ratio"] == 1.0 and ids["is_unique"] is True
    balance = customer["columns"]["balance"]
    assert 0 < balance["null_count"] < 1000  # nulls among the rows sampled
    assert balance["null_rate"] == balance["null_count"] / 1000
    assert prof["tables"]["product"]["sampled_rows"] == 300
    assert _profile(conn_str, sample_rows=0)["tables"]["customer"]["sampled_rows"] == 0


def test_the_sample_is_spread_and_repeatable_on_a_real_server(conn_str):
    import pyodbc

    first = _profile(conn_str)
    again = _profile(conn_str)
    assert first == again  # the same rows, run after run
    customer = first["tables"]["customer"]
    assert customer["sample_method"] == "checksum spread" and customer["sampled_rows"] == 1000
    ids = customer["columns"]["customer_id"]
    assert ids["max_value"][1] > 2000  # the first 1000 rows would stop at 1000
    assert first["sampling"]["method"].startswith("spread over the table")
    # the rows the server itself puts first by the scrambled CHECKSUM of the key are profiled
    with contextlib.closing(pyodbc.connect(conn_str, autocommit=True)) as conn:
        rows = conn.cursor().execute(
            "SELECT TOP 1000 customer_id FROM dbo.customer "
            "ORDER BY (CAST(CHECKSUM(customer_id) AS bigint) * 1327217885) % 2147483647, "
            "customer_id"
        )
        picked = [r[0] for r in rows.fetchall()]
    assert ids["min_value"][1] == min(picked) and ids["max_value"][1] == max(picked)
    assert ids["mean"] == pytest.approx(sum(picked) / 1000)
    assert first["tables"]["product"]["sample_method"] == "all rows"  # 300 rows, read whole


def test_a_heap_with_an_unhashable_column_is_spread_on_a_real_server(conn_str):
    prof = _profile(conn_str, schema="hp")["tables"]["events"]
    assert prof["sample_method"] == "checksum spread" and prof["sampled_rows"] == 1000
    assert prof["columns"]["a"]["max_value"][1] > 1500  # not the first 1000 rows
    assert prof["primary_key"] == []  # a heap with no id-like column


def test_no_sampled_rows_is_unknown_not_zero_on_a_real_server(conn_str):
    prof = _profile(conn_str, sample_rows=0)
    for col in prof["tables"]["customer"]["columns"].values():
        assert (col["null_rate"], col["cardinality_ratio"], col["is_unique"]) == (None,) * 3
    audit = _profile(conn_str)["tables"]["audit_log"]["columns"]["id"]  # an empty table
    assert (audit["null_rate"], audit["cardinality_ratio"], audit["is_unique"]) == (None,) * 3


def test_is_unique_is_null_aware_and_needs_two_values_on_a_real_server(conn_str):
    import pyodbc

    with contextlib.closing(pyodbc.connect(conn_str, autocommit=True)) as conn:
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS hp.uniq")
        cur.execute("CREATE TABLE hp.uniq (v int NULL, one int NULL)")
        cur.executemany(
            "INSERT INTO hp.uniq VALUES (?, ?)",
            [(None if i % 5 == 0 else i, 7 if i == 0 else None) for i in range(100)],
        )
    cols = _profile(conn_str, schema="hp", tables=["uniq"])["tables"]["uniq"]["columns"]
    assert cols["v"]["null_rate"] == 0.2 and cols["v"]["is_unique"] is True
    assert cols["one"]["is_unique"] is None  # a single non-null value
    one_row = _profile(conn_str, schema="hp", tables=["uniq"], sample_rows=1)
    assert {c["is_unique"] for c in one_row["tables"]["uniq"]["columns"].values()} == {None}


def test_undeclared_keys_are_inferred_beside_declared_ones_on_a_real_server(conn_str):
    prof = _profile(conn_str, schema="mix")
    assert [(r["name"], r["evidence"]) for r in prof["relationships"]] == [
        ("fk_orders_customer", "declared"),
        ("fk_orders_product_id", "data"),
        ("fk_orders_region_key", "name"),
    ]
    cols = prof["tables"]["orders"]["columns"]
    assert [cols[c]["fk_evidence"] for c in ("customer_id", "product_id", "region_key")] == [
        "declared",
        "data",
        "name",
    ]


def test_paid_and_valid_are_not_keys_and_guessed_keys_skip_foreign_keys_on_a_real_server(
    conn_str,
):
    prof = _profile(conn_str, schema="kn")
    invoices = prof["tables"]["invoices"]
    assert set(invoices["detected_fks"]) == {"customer_id"}  # not paid, not valid
    assert invoices["primary_key"] == ["invoice_id"]
    assert prof["tables"]["notes"]["primary_key"] == []
    assert prof["tables"]["customer"]["primary_key"] == ["customer_id"]


def test_a_relationship_the_data_shows_is_reported_on_a_real_server(conn_str):
    prof = _profile(conn_str, schema="idn")
    assert prof["tables"]["orders"]["detected_fks"] == {"customer_id": "customer"}
    assert [r["name"] for r in prof["relationships"]] == ["fk_orders_customer_id"]
    col = prof["tables"]["orders"]["columns"]["customer_id"]
    assert col["is_foreign_key"] is True and col["fk_ref_table"] == "customer"


def test_name_inferred_keys_mark_the_column_on_a_real_server(conn_str):
    prof = _profile(conn_str, schema="nom")
    assert prof["tables"]["orders"]["detected_fks"] == {"customer_id": "customer"}
    assert [r["name"] for r in prof["relationships"]] == ["fk_orders_customer_id"]
    col = prof["tables"]["orders"]["columns"]["customer_id"]
    assert col["is_foreign_key"] is True and col["fk_ref_table"] == "customer"
    wh = _profile(conn_str, schema="wh")["tables"]["factsales"]
    assert wh["columns"]["customer_key"]["is_foreign_key"] is True
    assert wh["detected_fks"] == {"customer_key": "dimcustomer", "product_key": "dimproduct"}


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
