"""End to end against real PostgreSQL 16 and MySQL 8.4 (the nightly job's containers).

    docker compose -f ci/emulators/docker-compose.yml up -d --wait postgres mysql
    SHAPE_POSTGRES_PASSWORD=shape_emulator SHAPE_MYSQL_PASSWORD=shape_emulator \\
        pytest -m emulator plugins/shape-databases/tests

Needs the drivers (``pip install -e 'plugins/shape-databases[postgres,mysql]'``).
``SHAPE_TEST_POSTGRES`` / ``SHAPE_TEST_MYSQL`` override the compose servers' URIs; the password
comes from ``SHAPE_POSTGRES_PASSWORD`` / ``SHAPE_MYSQL_PASSWORD`` like in production. Nothing
here is skipped when a server is missing: the nightly job must fail if it cannot reach it.
"""

import datetime as dt
import os
import threading
import time
import uuid
from decimal import Decimal

import pyarrow as pa
import pytest
from shape_databases import MySqlSink, PostgresSink, WriteError
from shape_databases.testing import sample_batch

from shape.errors import ShapeError

pytestmark = pytest.mark.emulator

PG_URI = os.environ.get("SHAPE_TEST_POSTGRES", "postgresql://shape@localhost:5432/shape")
MY_URI = os.environ.get("SHAPE_TEST_MYSQL", "mysql://shape@localhost:3306/shape")


def _wait(connect, what):
    deadline = time.monotonic() + 120
    while True:
        try:
            return connect()
        except Exception:
            if time.monotonic() > deadline:
                raise
            time.sleep(3)


def _pg_conn():
    import psycopg

    return _wait(
        lambda: psycopg.connect(
            host="localhost",
            port=5432,
            user="shape",
            dbname="shape",
            password=os.environ["SHAPE_POSTGRES_PASSWORD"],
            autocommit=True,
        ),
        "postgres",
    )


def _my_conn():
    import pymysql

    return _wait(
        lambda: pymysql.connect(
            host="127.0.0.1",
            port=3306,
            user="shape",
            database="shape",
            password=os.environ["SHAPE_MYSQL_PASSWORD"],
            autocommit=True,
        ),
        "mysql",
    )


@pytest.fixture
def name():
    return "t_" + uuid.uuid4().hex[:12]


@pytest.fixture
def pg():
    conn = _pg_conn()
    yield conn
    conn.close()


@pytest.fixture
def my():
    conn = _my_conn()
    yield conn
    conn.close()


def _query(conn, sql):
    cur = conn.cursor()
    cur.execute(sql)
    rows = [tuple(r) for r in cur.fetchall()]
    cur.close()
    return rows


def _drop(conn, name):
    q = '"' if conn.__class__.__module__.startswith("psycopg") else "`"
    cur = conn.cursor()
    cur.execute(f"DROP TABLE IF EXISTS {q}{name.replace(q, q + q)}{q}")
    cur.close()


# -- PostgreSQL ------------------------------------------------------------------------------
def test_postgres_copy_round_trip_and_modes(pg, name):
    sink = PostgresSink()
    try:
        assert (
            sink.write(
                PG_URI, name, iter([sample_batch(0, 4), sample_batch(4, 3)]), primary_key=["id"]
            )
            == 7
        )
        rows = _query(
            pg, f'SELECT id, name, score, price, active, born, seen, blob FROM "{name}" ORDER BY id'
        )
        assert [r[0] for r in rows] == list(range(7))
        assert rows[3][1:] == (
            "n3",
            1.5,
            Decimal("0.75"),
            False,
            dt.date(2000, 1, 4),
            dt.datetime(2024, 1, 1, 15),
            b"\x03",
        )
        with pytest.raises(ShapeError, match="already exists"):
            sink.write(PG_URI, name, iter([sample_batch()]))
        assert sink.write(PG_URI, name, iter([sample_batch(100, 2)]), write_mode="append") == 2
        assert sink.write(PG_URI, name, iter([sample_batch(200, 1)]), write_mode="truncate") == 1
        assert _query(pg, f'SELECT count(*) FROM "{name}"') == [(1,)]
        assert sink.write(PG_URI, name, iter([sample_batch(300, 2)]), write_mode="replace") == 2
        assert _query(pg, f'SELECT count(*) FROM "{name}"') == [(2,)]
    finally:
        _drop(pg, name)


def test_postgres_failure_rolls_back_the_table_too(pg, name):
    dup = pa.RecordBatch.from_pydict({"id": [1, 1]})
    with pytest.raises(WriteError, match="duplicate key"):
        PostgresSink().write(PG_URI, name, iter([dup]), primary_key=["id"])
    assert _query(pg, f"SELECT to_regclass('public.\"{name}\"')") == [(None,)]


def test_postgres_hostile_names_and_schema(pg, name):
    hostile = 'x"; DROP TABLE users; --'
    schema = "s_" + name
    batch = pa.RecordBatch.from_pydict({hostile: [1, 2], "ok": ["a", "b"]})
    try:
        written = PostgresSink().write(
            PG_URI, name, iter([batch]), schema_name=schema, table_prefix="p "
        )
        assert written == 2
        cols = _query(
            pg,
            "SELECT column_name FROM information_schema.columns "
            f"WHERE table_schema = '{schema}' AND table_name = 'p {name}' "
            "ORDER BY ordinal_position",
        )
        assert cols == [(hostile,), ("ok",)]
    finally:
        cur = pg.cursor()
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        cur.close()


def test_postgres_commit_rows_makes_rows_visible_during_the_stream(pg, name):
    gate = threading.Event()
    seen: list[int] = []

    def stream():
        yield sample_batch(0, 6)
        # commit_rows=3: two COPYs of 3 rows are committed before the next batch is asked for
        seen.append(_query(pg, f'SELECT count(*) FROM "{name}"')[0][0])
        gate.set()
        yield sample_batch(6, 2)

    try:
        assert PostgresSink().write(PG_URI, name, stream(), commit_rows=3, batch_size=3) == 8
        assert gate.is_set() and seen == [6]
        assert _query(pg, f'SELECT count(*) FROM "{name}"') == [(8,)]
    finally:
        _drop(pg, name)


def test_postgres_streams_a_large_table(pg, name):
    def stream():
        for i in range(100):
            yield sample_batch(i * 1000, 1000)

    try:
        assert PostgresSink().write(PG_URI, name, stream(), commit_rows=20000) == 100000
        assert _query(pg, f'SELECT count(*), max(id) FROM "{name}"') == [(100000, 99999)]
    finally:
        _drop(pg, name)


def test_postgres_wrong_password_fails_and_leaks_nothing(name):
    secret = "definitely-wrong-pw-" + uuid.uuid4().hex
    with pytest.raises(WriteError) as info:
        PostgresSink().write(PG_URI, name, iter([sample_batch()]), password=secret)
    assert secret not in str(info.value) and secret not in repr(info.value)


# -- MySQL -----------------------------------------------------------------------------------
def test_mysql_insert_round_trip_and_modes(my, name):
    sink = MySqlSink()
    try:
        assert (
            sink.write(
                MY_URI,
                name,
                iter([sample_batch(0, 4), sample_batch(4, 3)]),
                primary_key=["id"],
                batch_size=3,
            )
            == 7
        )
        rows = _query(
            my,
            "SELECT id, name, score, price, active, born, seen, hex(`blob`) "
            f"FROM `{name}` ORDER BY id",
        )
        assert [r[0] for r in rows] == list(range(7))
        assert rows[3][1:] == (
            "n3",
            1.5,
            Decimal("0.75"),
            0,
            dt.date(2000, 1, 4),
            dt.datetime(2024, 1, 1, 15),
            "03",
        )
        with pytest.raises(ShapeError, match="already exists"):
            sink.write(MY_URI, name, iter([sample_batch()]))
        assert sink.write(MY_URI, name, iter([sample_batch(100, 2)]), write_mode="append") == 2
        assert sink.write(MY_URI, name, iter([sample_batch(200, 1)]), write_mode="truncate") == 1
        assert _query(my, f"SELECT count(*) FROM `{name}`") == [(1,)]
        assert sink.write(MY_URI, name, iter([sample_batch(300, 2)]), write_mode="replace") == 2
        assert _query(my, f"SELECT count(*) FROM `{name}`") == [(2,)]
    finally:
        _drop(my, name)


def test_mysql_failure_rolls_back_rows_and_drops_the_new_table(my, name):
    dup = pa.RecordBatch.from_pydict({"id": [1, 1]})
    with pytest.raises(WriteError, match="Duplicate entry"):
        MySqlSink().write(MY_URI, name, iter([dup]), primary_key=["id"])
    assert _query(
        my, f"SELECT count(*) FROM information_schema.tables WHERE table_name = '{name}'"
    ) == [(0,)]


def test_mysql_commit_rows_keeps_what_was_committed_on_failure(my, name):
    batch = pa.RecordBatch.from_pydict({"id": [1, 2, 3, 4, 5, 3]})
    with pytest.raises(WriteError, match="3 rows were committed") as info:
        MySqlSink().write(
            MY_URI, name, iter([batch]), primary_key=["id"], commit_rows=3, batch_size=3
        )
    assert info.value.rows_committed == 3
    try:
        assert _query(my, f"SELECT count(*) FROM `{name}`") == [(3,)]
    finally:
        _drop(my, name)


def test_mysql_hostile_names_and_non_finite_floats(my, name):
    hostile = "x`; DROP TABLE users; --"
    batch = pa.RecordBatch.from_pydict({hostile: [1.0, float("nan")], "ok": ["a", "b"]})
    try:
        assert MySqlSink().write(MY_URI, name, iter([batch]), table_prefix="p ") == 2
        cols = _query(
            my,
            "SELECT column_name FROM information_schema.columns "
            f"WHERE table_schema = 'shape' AND table_name = 'p {name}' ORDER BY ordinal_position",
        )
        assert cols == [(hostile,), ("ok",)]
        quoted = hostile.replace("`", "``")
        assert _query(my, f"SELECT `{quoted}` FROM `p {name}` ORDER BY ok") == [(1.0,), (None,)]
    finally:
        _drop(my, f"p {name}")


def test_mysql_wrong_password_fails_and_leaks_nothing(name):
    secret = "definitely-wrong-pw-" + uuid.uuid4().hex
    with pytest.raises(WriteError) as info:
        MySqlSink().write(MY_URI, name, iter([sample_batch()]), password=secret)
    assert secret not in str(info.value) and secret not in repr(info.value)


@pytest.mark.parametrize("dialect", ["postgres", "mysql"])
def test_w9_03_catalog_types_and_utc_microsecond_roundtrip(dialect, name):
    """Service catalog and readback enforce the write maps, including a negative boundary."""
    from shape.repro import dataset_id

    conn = _pg_conn() if dialect == "postgres" else _my_conn()
    sink = PostgresSink() if dialect == "postgres" else MySqlSink()
    uri = PG_URI if dialect == "postgres" else MY_URI
    schema = pa.schema(
        [("small", pa.int16()), ("amount", pa.decimal128(12, 4)), ("at", pa.timestamp("us", "UTC"))]
    )
    value = dt.datetime(2024, 1, 2, 3, 4, 5, 999999, tzinfo=dt.timezone(dt.timedelta(hours=9)))
    batch = pa.RecordBatch.from_pydict(
        {
            "small": [-32768, 32767],
            "amount": [Decimal("-99999999.9999"), Decimal("0.0001")],
            "at": [value, None],
        },
        schema=schema,
    )
    q = '"' if dialect == "postgres" else "`"
    try:
        sink.write(uri, name, [batch])
        cur = conn.cursor()
        cur.execute("SET TIME ZONE 'UTC'" if dialect == "postgres" else "SET time_zone = '+00:00'")
        cur.execute(
            "SELECT column_name, data_type, numeric_precision, numeric_scale "
            "FROM information_schema.columns WHERE table_name = %s ORDER BY ordinal_position",
            [name],
        )
        columns = cur.fetchall()
        assert columns[0][1] == "smallint"
        assert columns[1][2:] == (12, 4)
        if dialect == "postgres":
            assert columns[2][1] == "timestamp with time zone"
        else:
            assert columns[2][1] == "timestamp"
        cur.execute(f"SELECT * FROM {q}{name}{q} ORDER BY small")  # nosec B608 - unique generated name
        rows = cur.fetchall()
        cur.close()
        read = pa.Table.from_pylist(
            [dict(zip(schema.names, r, strict=True)) for r in rows], schema=schema
        )
        assert read["at"][0].as_py() == value.astimezone(dt.UTC)
        assert read["at"][0].as_py().utcoffset() == dt.timedelta(0)
        assert dataset_id({"items": read}) == dataset_id({"items": pa.Table.from_batches([batch])})
    finally:
        _drop(conn, name)
        conn.close()
