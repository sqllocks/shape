"""Hostile identifiers, the Arrow -> database type map, and the statements each sink sends."""

import math

import pyarrow as pa
import pytest
from shape_databases import MySqlSink, PostgresSink, _sql
from shape_databases.testing import FakeServer, sample_batch

from shape.errors import ShapeError

PG = "postgresql://shape@h/db"
MY = "mysql://shape@h/db"

HOSTILE = [
    'x"; DROP TABLE users; --',
    "x`; DROP TABLE users; --",
    "x'; DROP TABLE users; --",
    "a\nb",
    "semi;colon",
    "unicode_é中",
    "x]; DROP TABLE users; --",
    "--",
    "/* c */",
]


@pytest.mark.parametrize("name", HOSTILE)
def test_a_hostile_table_name_is_quoted_as_one_identifier(flavour, name):
    sink, uri, server = flavour
    sink.write(uri, name, iter([pa.RecordBatch.from_pydict({"v": [1]})]))
    create = next(s for s in server.statements() if s.startswith("CREATE TABLE"))
    q = '"' if sink.dialect == "postgres" else "`"
    quoted = q + name.replace(q, q + q) + q
    assert create.startswith(f"CREATE TABLE {quoted} (")
    # the only statements sent are the ones the sink writes, each about the one quoted table
    for sql in server.statements():
        assert sql.startswith(("SELECT 1 FROM information_schema", "CREATE TABLE"))
    assert server.rows(name) == [(1,)]


@pytest.mark.parametrize("name", HOSTILE)
def test_hostile_column_and_schema_names_are_quoted(flavour, name):
    sink, uri, server = flavour
    batch = pa.RecordBatch.from_pydict({name: [1], "ok": [2]})
    sink.write(uri, "t", iter([batch]), schema_name=name)
    q = '"' if sink.dialect == "postgres" else "`"
    quoted = q + name.replace(q, q + q) + q
    create = next(s for s in server.statements() if s.startswith("CREATE TABLE"))
    assert create.startswith(f"CREATE TABLE {quoted}.")
    assert f"  {quoted} " in create


@pytest.mark.parametrize(
    ("dialect", "name"),
    [
        ("postgres", ""),
        ("postgres", "a\x00b"),
        ("postgres", "x" * 64),
        ("postgres", "é" * 32),  # 64 bytes
        ("mysql", "x" * 65),
        ("mysql", "tail "),
        ("mysql", "pct%s"),
    ],
)
def test_names_the_database_would_change_or_misread_are_refused(dialect, name):
    with pytest.raises(ShapeError):
        _sql.check_identifier(name, "table", dialect)


def test_the_length_limits_are_exact():
    assert _sql.check_identifier("x" * 63, "table", "postgres")
    assert _sql.check_identifier("x" * 64, "table", "mysql")


def test_a_refused_name_stops_the_write_before_any_connection(flavour):
    sink, uri, server = flavour
    with pytest.raises(ShapeError):
        sink.write(uri, "x" * 80, iter([sample_batch()]))
    with pytest.raises(ShapeError):
        sink.write(uri, "t", iter([sample_batch()]), schema_name="a\x00")
    with pytest.raises(ShapeError):
        sink.write(uri, "t", iter([sample_batch()]), primary_key=["id\x00"])
    with pytest.raises(ShapeError, match="primary key column"):
        sink.write(uri, "t", iter([sample_batch()]), primary_key=["nope"])
    assert not any(e[0] == "execute" and e[1].startswith("CREATE") for e in server.events)


def test_the_prefix_counts_toward_the_length_limit(flavour):
    sink, uri, _ = flavour
    with pytest.raises(ShapeError):
        sink.write(uri, "t" * 40, iter([sample_batch()]), table_prefix="p" * 40)


def test_mysql_refuses_a_percent_in_a_column_name():
    server = FakeServer("mysql")
    batch = pa.RecordBatch.from_pydict({"a%s": [1]})
    with pytest.raises(ShapeError, match="'%'"):
        MySqlSink(connect=server.connect).write(MY, "t", iter([batch]))


def test_postgres_allows_a_percent_because_copy_takes_no_parameters():
    server = FakeServer("postgres")
    batch = pa.RecordBatch.from_pydict({"a%s": [1]})
    assert PostgresSink(connect=server.connect).write(PG, "t", iter([batch])) == 1


# -- type mapping --------------------------------------------------------------------------
TYPES = [
    (pa.int8(), "SMALLINT", "TINYINT"),
    (pa.int64(), "BIGINT", "BIGINT"),
    (pa.uint32(), "BIGINT", "BIGINT"),
    (pa.float32(), "REAL", "FLOAT"),
    (pa.float64(), "DOUBLE PRECISION", "DOUBLE"),
    (pa.decimal128(12, 3), "NUMERIC(12,3)", "DECIMAL(12,3)"),
    (pa.bool_(), "BOOLEAN", "TINYINT(1)"),
    (pa.date32(), "DATE", "DATE"),
    (pa.timestamp("us"), "TIMESTAMP(6)", "DATETIME(6)"),
    (pa.timestamp("us", tz="UTC"), "TIMESTAMPTZ(6)", "TIMESTAMP(6)"),
    (pa.time64("us"), "TIME(6)", "TIME(6)"),
    (pa.binary(), "BYTEA", "LONGBLOB"),
    (pa.large_binary(), "BYTEA", "LONGBLOB"),
    (pa.string(), "TEXT", "TEXT"),
    (pa.large_string(), "TEXT", "TEXT"),
    (pa.list_(pa.int64()), "TEXT", "LONGTEXT"),
    (pa.struct([("a", pa.int64())]), "TEXT", "LONGTEXT"),
]


@pytest.mark.parametrize(("arrow", "pg", "my"), TYPES)
def test_arrow_types_map_to_the_script_dialect_types(arrow, pg, my):
    for dialect, want in (("postgres", pg), ("mysql", my)):
        schema = pa.schema([pa.field("c", arrow)])
        ddl = _sql.create_table_sql(None, "t", schema, dialect)
        assert f" {want} NULL" in ddl, ddl


def test_the_types_are_core_sql_sinks_own():
    """Same mapping as the script sink, so a live table equals the scripted one."""
    import tempfile
    from pathlib import Path

    from shape.builtins.sinks.sql import SqlSink

    batch = sample_batch()
    for dialect in ("postgres", "mysql"):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "t.sql"
            SqlSink().write(str(out), "t", iter([batch]), sql_dialect=dialect, ddl=True, drop=False)
            script = out.read_text(encoding="utf-8")
        live = _sql.create_table_sql(None, "t", batch.schema, dialect, first=batch)
        quote = '"' if dialect == "postgres" else "`"
        for name, sql_type, _ in _sql.column_definitions(batch.schema, batch, {}, [], dialect):
            assert f"{quote}{name}{quote}" in script
            assert sql_type in script and sql_type in live


def test_column_metadata_overrides_like_the_script_sink():
    schema = pa.schema([pa.field("s", pa.string()), pa.field("d", pa.decimal128(5, 1))])
    meta = {"s": {"max_length": 40, "nullable": False}, "d": {"precision": 9, "scale": 4}}
    ddl = _sql.create_table_sql(None, "t", schema, "postgres", columns=meta)
    assert '"s" VARCHAR(40) NOT NULL' in ddl and "NUMERIC(5,1)" in ddl


# -- the statements -------------------------------------------------------------------------
def test_postgres_streams_with_copy_from_stdin():
    server = FakeServer("postgres")
    PostgresSink(connect=server.connect).write(PG, "customer", iter([sample_batch()]))
    statement, rows = server.copies()[0]
    assert statement.startswith('COPY "customer" ("id", "name", ')
    assert statement.endswith(") FROM STDIN")
    assert len(rows) == 3 and rows[0][0] == 0
    assert server.statements("executemany") == []


def test_postgres_connect_parameters_come_from_the_uri():
    server = FakeServer("postgres")
    uri = "postgresql://ann%40corp@db.example:6543/my%20db?sslmode=verify-full&connect_timeout=7"
    PostgresSink(connect=server.connect).write(uri, "t", iter([sample_batch()]), password="pw-123")
    params = server.events[0][1]
    assert params == {
        "sslmode": "verify-full",
        "connect_timeout": 7,
        "host": "db.example",
        "port": 6543,
        "user": "ann@corp",
        "dbname": "my db",
        "application_name": "shape",
        "password": "pw-123",
    }


def test_the_postgres_scheme_alias_works():
    server = FakeServer("postgres")
    assert PostgresSink(connect=server.connect).write(
        "postgres://u@h/d", "t", iter([sample_batch()])
    )


def test_mysql_sends_bound_multi_row_inserts_and_never_loads_a_local_file():
    server = FakeServer("mysql")
    MySqlSink(connect=server.connect).write(
        MY + "?ssl_ca=/etc/ca.pem&ssl_verify_cert=true",
        "customer",
        iter([sample_batch(0, 5)]),
        batch_size=2,
    )
    params = server.events[0][1]
    assert params["local_infile"] is False and params["autocommit"] is False
    assert params["charset"] == "utf8mb4" and params["ssl_ca"] == "/etc/ca.pem"
    assert params["ssl_verify_cert"] is True
    inserts = [e for e in server.events if e[0] == "executemany"]
    assert [len(e[2]) for e in inserts] == [2, 2, 1]
    assert inserts[0][1].startswith("INSERT INTO `customer` (`id`, `name`")
    assert inserts[0][1].endswith("VALUES (%s, %s, %s, %s, %s, %s, %s, %s)")
    assert not any("LOAD DATA" in s for s in server.statements())


def test_mysql_writes_non_finite_floats_as_null_like_the_script_dialect():
    server = FakeServer("mysql")
    batch = pa.RecordBatch.from_pydict({"x": [1.0, math.nan, math.inf, None]})
    MySqlSink(connect=server.connect).write(MY, "t", iter([batch]))
    assert server.rows("t") == [(1.0,), (None,), (None,), (None,)]


def test_postgres_keeps_non_finite_floats():
    server = FakeServer("postgres")
    batch = pa.RecordBatch.from_pydict({"x": [math.inf]})
    PostgresSink(connect=server.connect).write(PG, "t", iter([batch]))
    assert server.rows("t") == [(math.inf,)]


def test_unknown_uri_parameters_and_schemes_are_refused(flavour):
    sink, uri, server = flavour
    for bad in (
        uri.split("?")[0] + "?options=-c%20statement_timeout=1",
        uri.split("?")[0] + "?host=evil",
        "http://h/d",
        "file:///tmp/x",
    ):
        with pytest.raises(ShapeError):
            sink.write(bad, "t", iter([sample_batch()]))
    with pytest.raises(ShapeError):
        PostgresSink(connect=server.connect).write(
            "postgresql://h/d?sslmode=sometimes", "t", iter([sample_batch()])
        )
    assert server.events == []


@pytest.mark.parametrize(
    ("uri", "message"),
    [
        ("postgresql://u@h/db?sslmode=bogus", r"URI parameter 'sslmode'.*'bogus'.*one of disable"),
        ("postgresql://u@h/db?connect_timeout=x", r"URI parameter 'connect_timeout'.*integer"),
        ("mysql://u@h/db?ssl_verify_cert=maybe", r"URI parameter 'ssl_verify_cert'.*true or false"),
    ],
)
def test_a_bad_uri_parameter_value_names_the_parameter(uri, message):
    # Issue #344: the error was only "must be one of ..." with no parameter name.
    sink = PostgresSink() if uri.startswith("postgresql") else MySqlSink()
    with pytest.raises(ShapeError, match=message):
        sink.write(uri, "t", iter([sample_batch()]))
