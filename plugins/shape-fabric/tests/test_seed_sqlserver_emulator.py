"""`shape seed` against a real SQL Server (the nightly ``sqlserver-e2e`` job's container).

    docker compose -f ci/emulators/docker-compose.yml up -d --wait mssql
    pytest -m emulator plugins/shape-fabric/tests/test_seed_sqlserver_emulator.py

Seeds the retail domain into its own schema, checks the row counts, the refusal of a second
``create`` (before anything is written) and that ``truncate`` with the same seed leaves the same
rows. Nothing is skipped when the server is missing: the nightly job must fail if it cannot
reach it.
"""

import os
import re
import uuid

import pytest
from shape_fabric import _tsql

from shape.testdata.seed import SeedRefused, seed_target

pytestmark = pytest.mark.emulator

CS = os.environ.get(
    "SHAPE_TEST_MSSQL",
    "Driver={ODBC Driver 18 for SQL Server};Server=localhost,1433;UID=sa;PWD=Shape_Emulator_1;"
    "Encrypt=yes;TrustServerCertificate=yes",
)
TABLES = [
    "customer",
    "address",
    "product_category",
    "product",
    "promotion",
    "store",
    "order",
    "order_line",
    "return",
]


def _field(name):
    match = re.search(rf"(?i)(?:^|;)\s*{name}\s*=\s*([^;]*)", CS)
    assert match, f"SHAPE_TEST_MSSQL has no {name}"
    return match.group(1).strip()


URI = "mssql://" + _field("Server").replace(",", ":") + "/master?trust_server_certificate=true"
LOGIN = {"user": _field("UID"), "password": _field("PWD")}


@pytest.fixture
def schema():
    name = f"shape_seed_{uuid.uuid4().hex[:10]}"
    conn = _tsql.connect(CS)
    cursor = conn.cursor()
    cursor.execute(f"EXEC('CREATE SCHEMA {_tsql.ident(name)}')")
    conn.commit()
    yield name, conn
    for table in reversed(TABLES):
        cursor.execute(f"DROP TABLE IF EXISTS {_tsql.qualified(name, table)}")
    cursor.execute(f"DROP SCHEMA IF EXISTS {_tsql.ident(name)}")
    conn.commit()
    conn.close()


def _rows(conn, schema, table):
    cursor = conn.cursor()
    cursor.execute(f"SELECT * FROM {_tsql.qualified(schema, table)} ORDER BY 1")
    return [tuple(map(str, row)) for row in cursor.fetchall()]


def test_seed_into_sql_server(schema):
    name, conn = schema
    options = {**LOGIN, "schema_name": name}
    seed_target("retail", URI, scale="tiny", seed=5, sink_options=options)
    assert [len(_rows(conn, name, t)) for t in TABLES] == [100] * 9
    with pytest.raises(SeedRefused, match="already exist"):
        seed_target("retail", URI, scale="tiny", seed=5, sink_options=options)
    first = _rows(conn, name, "order")
    seed_target("retail", URI, scale="tiny", seed=5, mode="truncate", sink_options=options)
    assert _rows(conn, name, "order") == first


def test_create_checks_every_table_before_writing(schema):
    name, conn = schema
    conn.cursor().execute(f"CREATE TABLE {_tsql.qualified(name, 'return')} (return_id BIGINT)")
    conn.commit()
    with pytest.raises(SeedRefused, match="return"):
        seed_target("retail", URI, scale="tiny", sink_options={**LOGIN, "schema_name": name})
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = ? "
        "AND TABLE_NAME = 'customer'",
        name,
    )
    assert cursor.fetchone()[0] == 0
