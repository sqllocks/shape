"""Real PostgreSQL/MySQL constraint enforcement and mode-independent defaults."""

import os
import uuid

import pyarrow as pa
import pytest
from shape_databases import MySqlSink, PostgresSink, WriteError

pytestmark = pytest.mark.emulator


@pytest.fixture(params=["postgres", "mysql"])
def database(request):
    dialect = request.param
    if dialect == "postgres":
        import psycopg

        conn = psycopg.connect(
            host="localhost",
            user="shape",
            dbname="shape",
            password=os.environ["SHAPE_POSTGRES_PASSWORD"],
            autocommit=True,
        )
        sink = PostgresSink()
        uri = "postgresql://shape@localhost:5432/shape"
    else:
        import pymysql

        conn = pymysql.connect(
            host="127.0.0.1",
            user="shape",
            database="shape",
            password=os.environ["SHAPE_MYSQL_PASSWORD"],
            autocommit=True,
        )
        sink = MySqlSink()
        uri = "mysql://shape@localhost:3306/shape"
    yield dialect, conn, sink, uri
    conn.close()


def quote(name, dialect):
    char = '"' if dialect == "postgres" else "`"
    return char + name.replace(char, char + char) + char


def execute(conn, sql, args=None):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall() if cur.description else []


def test_actual_foreign_unique_check_default_and_partial_commit(database):
    dialect, conn, sink, uri = database
    suffix = uuid.uuid4().hex[:12]
    parent, child = "cp_" + suffix, "cc_" + suffix
    parent_schema = pa.schema([("id", pa.int64())])
    child_schema = pa.schema(
        [("id", pa.int64()), ("parent_id", pa.int64()), ("amount", pa.int64())]
    )
    check_name, foreign_name = "ck_" + suffix, "fk_" + suffix
    options = {
        "schema": child_schema,
        "unique": [["id", "parent_id"]],
        "defaults": {"amount": 2},
        "checks": [
            {
                "name": check_name,
                "expression": {"op": ">=", "left": {"column": "amount"}, "right": {"literal": 0}},
            }
        ],
        "foreign_keys": [
            {
                "name": foreign_name,
                "columns": ["parent_id"],
                "ref_table": parent,
                "ref_columns": ["id"],
            }
        ],
    }
    try:
        sink.prepare_constraints(
            uri,
            {
                parent: {"schema": parent_schema, "primary_key": ["id"]},
                child: options,
            },
        )
        sink.write(
            uri,
            parent,
            [pa.RecordBatch.from_pydict({"id": [1]})],
            primary_key=["id"],
            prepared_constraints=True,
        )
        invalid = pa.RecordBatch.from_pydict({"id": [1, 2], "parent_id": [1, 1], "amount": [3, -1]})
        with pytest.raises(WriteError, match=check_name) as info:
            sink.write(uri, child, [invalid], **options, prepared_constraints=True, commit_rows=1)
        assert info.value.rows_committed == 1
        assert execute(conn, f"SELECT COUNT(*) FROM {quote(child, dialect)}")[0][0] == 1
        with pytest.raises(WriteError, match=foreign_name):
            sink.write(
                uri,
                child,
                [pa.RecordBatch.from_pydict({"id": [3], "parent_id": [999], "amount": [2]})],
                **options,
                prepared_constraints=True,
            )
        with pytest.raises(WriteError, match="[Dd]uplicate|[Uu]nique"):
            sink.write(
                uri,
                child,
                [pa.RecordBatch.from_pydict({"id": [1], "parent_id": [1], "amount": [2]})],
                **options,
                prepared_constraints=True,
            )
        execute(
            conn,
            f"INSERT INTO {quote(child, dialect)} ({quote('id', dialect)}, "
            f"{quote('parent_id', dialect)}) VALUES (%s, %s)",
            (4, 1),
        )
        assert (
            execute(
                conn,
                f"SELECT {quote('amount', dialect)} FROM {quote(child, dialect)} "
                f"WHERE {quote('id', dialect)} = %s",
                (4,),
            )[0][0]
            == 2
        )
    finally:
        execute(conn, f"DROP TABLE IF EXISTS {quote(child, dialect)}")
        execute(conn, f"DROP TABLE IF EXISTS {quote(parent, dialect)}")


@pytest.mark.parametrize("strict_mode", [False, True])
def test_actual_string_defaults_survive_sql_backslash_modes(database, strict_mode):
    dialect, conn, sink, uri = database
    table = "cd_" + uuid.uuid4().hex[:12]
    value = "C:\\tmp\\café 'quoted'"
    if dialect == "postgres":
        execute(
            conn,
            "SELECT set_config('standard_conforming_strings', %s, false)",
            ("on" if strict_mode else "off",),
        )
    else:
        execute(conn, "SET SESSION sql_mode = %s", ("NO_BACKSLASH_ESCAPES" if strict_mode else "",))
    try:
        sink.write(
            uri,
            table,
            [],
            schema=pa.schema([("value", pa.string())]),
            defaults={"value": value},
            connection=conn,
        )
        execute(
            conn,
            f"INSERT INTO {quote(table, dialect)} () VALUES ()"
            if dialect == "mysql"
            else f"INSERT INTO {quote(table, dialect)} DEFAULT VALUES",
        )
        assert (
            execute(conn, f"SELECT {quote('value', dialect)} FROM {quote(table, dialect)}")[0][0]
            == value
        )
    finally:
        execute(conn, f"DROP TABLE IF EXISTS {quote(table, dialect)}")


def test_existing_constraint_refusal_never_truncates_real_rows(database):
    from shape.errors import ShapeError

    dialect, conn, sink, uri = database
    table = "cr_" + uuid.uuid4().hex[:12]
    quoted = quote(table, dialect)
    batch = pa.RecordBatch.from_pydict({"id": [1]})
    try:
        sink.write(uri, table, [batch])
        with pytest.raises(ShapeError, match="requested constraints cannot yet be verified"):
            sink.write(uri, table, [batch], write_mode="truncate", unique=[["id"]])
        assert list(execute(conn, f"SELECT id FROM {quoted}")) == [(1,)]
    finally:
        execute(conn, f"DROP TABLE IF EXISTS {quoted}")
