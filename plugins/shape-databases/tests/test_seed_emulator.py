"""`shape seed` against real PostgreSQL 16 and MySQL 8.4 (the nightly job's containers).

    docker compose -f ci/emulators/docker-compose.yml up -d --wait postgres mysql
    SHAPE_POSTGRES_PASSWORD=shape_emulator SHAPE_MYSQL_PASSWORD=shape_emulator \\
        pytest -m emulator plugins/shape-databases/tests/test_seed_emulator.py

Each test seeds into a fresh database object name prefix and drops the tables afterwards. Nothing
here is skipped when a server is missing: the nightly job must fail if it cannot reach it.
"""

import os

import pytest

from shape.testdata.seed import SeedRefused, seed_target

pytestmark = pytest.mark.emulator

PG_URI = os.environ.get("SHAPE_TEST_POSTGRES", "postgresql://shape@localhost:5432/shape")
MY_URI = os.environ.get("SHAPE_TEST_MYSQL", "mysql://shape@localhost:3306/shape")
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


def _connect(flavour):
    if flavour == "postgres":
        import psycopg

        return psycopg.connect(
            host="localhost",
            port=5432,
            user="shape",
            dbname="shape",
            password=os.environ["SHAPE_POSTGRES_PASSWORD"],
            autocommit=True,
        )
    import pymysql

    return pymysql.connect(
        host="127.0.0.1",
        port=3306,
        user="shape",
        database="shape",
        password=os.environ["SHAPE_MYSQL_PASSWORD"],
        autocommit=True,
    )


@pytest.fixture(params=[("postgres", PG_URI, '"'), ("mysql", MY_URI, "`")])
def database(request):
    flavour, uri, q = request.param
    conn = _connect(flavour)
    cur = conn.cursor()

    def drop():
        for table in reversed(TABLES):
            cur.execute(f"DROP TABLE IF EXISTS {q}{table}{q}")

    drop()
    yield flavour, uri, conn, q
    drop()
    conn.close()


def _count(conn, q, table):
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {q}{table}{q}")
    return cur.fetchone()[0]


def _fingerprint(conn, q):
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM {q}order{q} ORDER BY 1")
    return [tuple(map(str, row)) for row in cur.fetchall()]


def test_seed_create_truncate_and_refusal_on_a_real_server(database):
    flavour, uri, conn, q = database
    seed_target("retail", uri, scale="tiny", seed=5)
    assert [_count(conn, q, t) for t in TABLES] == [100] * 9
    with pytest.raises(SeedRefused, match="already exist"):
        seed_target("retail", uri, scale="tiny", seed=5)
    first = _fingerprint(conn, q)
    seed_target("retail", uri, scale="tiny", seed=5, mode="truncate")
    assert _fingerprint(conn, q) == first
    seed_target("retail", uri, scale="tiny", seed=5, mode="append")
    assert _count(conn, q, "order") == 200


def test_seed_create_checks_all_tables_before_writing(database):
    flavour, uri, conn, q = database
    conn.cursor().execute(f"CREATE TABLE {q}return{q} (return_id BIGINT)")
    with pytest.raises(SeedRefused, match="return"):
        seed_target("retail", uri, scale="tiny")
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'customer'"
        + (
            " AND table_schema = current_schema()"
            if flavour == "postgres"
            else " AND table_schema = DATABASE()"
        )
    )
    assert cur.fetchone()[0] == 0
