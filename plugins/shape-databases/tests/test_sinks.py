"""Contract tests: the behaviour both sinks share, against the in-repo fake server."""

import datetime as dt

import pyarrow as pa
import pytest
from shape_databases import MySqlSink, PostgresSink, WriteError
from shape_databases.testing import SAMPLE_SCHEMA as SCHEMA
from shape_databases.testing import FakeServer
from shape_databases.testing import sample_batch as make_batch

from shape.errors import ShapeError

PG_URI = "postgresql://shape@db.example:5432/shape?sslmode=require"
MY_URI = "mysql://shape@db.example:3306/shape"


def _write(flavour, batches=None, **options):
    sink, uri, server = flavour
    batches = [make_batch()] if batches is None else batches
    return sink.write(uri, "customer", iter(batches), **options), server


def test_create_writes_the_table_and_returns_the_row_count(flavour):
    rows, server = _write(flavour, [make_batch(0, 3), make_batch(3, 2)])
    assert rows == 5
    assert [r[0] for r in server.rows("customer")] == [0, 1, 2, 3, 4]
    assert server.columns[server.key(None, "customer")] == list(SCHEMA.names)
    assert server.events[-1] == ("close",)  # the connection the sink opened is closed


def test_the_default_mode_never_touches_an_existing_table(flavour):
    _, server = _write(flavour)
    sink, uri, _ = flavour
    before = list(server.events)
    with pytest.raises(ShapeError, match="already exists"):
        sink.write(uri, "customer", iter([make_batch(10, 2)]))
    assert len(server.rows("customer")) == 3
    assert not any(
        e[0] == "execute" and e[1].startswith(("DROP", "TRUNCATE"))
        for e in server.events[len(before) :]
    )


def test_append_adds_rows_and_creates_a_missing_table(flavour):
    _, server = _write(flavour, write_mode="append")
    sink, uri, _ = flavour
    sink.write(uri, "customer", iter([make_batch(10, 2)]), write_mode="append")
    assert len(server.rows("customer")) == 5


def test_truncate_empties_then_writes(flavour):
    _, server = _write(flavour)
    sink, uri, _ = flavour
    sink.write(uri, "customer", iter([make_batch(10, 2)]), write_mode="truncate")
    assert [r[0] for r in server.rows("customer")] == [10, 11]


def test_replace_drops_and_recreates(flavour):
    _, server = _write(flavour)
    sink, uri, _ = flavour
    sink.write(uri, "customer", iter([make_batch(10, 1)]), write_mode="replace")
    assert [r[0] for r in server.rows("customer")] == [10]
    assert any("DROP TABLE IF EXISTS" in s for s in server.statements())


def test_unknown_mode_batch_size_and_commit_rows_are_refused_before_connecting(flavour):
    sink, uri, server = flavour
    for bad in (
        {"write_mode": "upsert"},
        {"batch_size": 0},
        {"batch_size": True},
        {"commit_rows": 0},
        {"commit_rows": "5"},
    ):
        with pytest.raises(ShapeError):
            sink.write(uri, "t", iter([make_batch()]), **bad)
    assert server.events == []


def test_schema_name_and_table_prefix(flavour):
    sink, uri, server = flavour
    sink.write(uri, "customer", iter([make_batch()]), schema_name="app", table_prefix="gen_")
    assert server.rows("gen_customer", "app")
    created = [s for s in server.statements() if s.startswith("CREATE TABLE")]
    assert "app" in created[0] and "gen_customer" in created[0]
    assert server.statements()[0].startswith("SELECT 1 FROM information_schema.tables")


def test_a_table_is_created_from_the_arrow_schema_with_keys_and_nullability(flavour):
    sink, uri, server = flavour
    sink.write(uri, "customer", iter([make_batch()]), primary_key=["id"])
    ddl = next(s for s in server.statements() if s.startswith("CREATE TABLE"))
    assert "NOT NULL" in ddl.splitlines()[1]  # id: not nullable in the schema, and the key
    assert "PRIMARY KEY" in ddl


def test_an_empty_stream_needs_a_schema_and_then_creates_an_empty_table(flavour):
    sink, uri, server = flavour
    with pytest.raises(ShapeError, match="no batches and no schema"):
        sink.write(uri, "t", iter([]))
    assert sink.write(uri, "t", iter([]), schema=SCHEMA) == 0
    assert server.rows("t") == []
    assert server.key(None, "t") in server.tables


def test_batches_must_keep_the_same_columns(flavour):
    sink, uri, server = flavour
    other = pa.RecordBatch.from_pydict({"x": [1]})
    with pytest.raises(ShapeError, match="has columns"):
        sink.write(uri, "t", iter([make_batch(), other]))
    assert server.rows("t") == []  # rolled back, and the table this call made is gone
    assert server.key(None, "t") not in server.tables


def test_a_failure_rolls_everything_back_and_raises_write_error(flavour):
    sink, uri, _ = flavour
    server = FakeServer(sink.dialect, fail_after_rows=4)
    sink = type(sink)(connect=server.connect)
    with pytest.raises(WriteError, match="simulated driver failure") as info:
        sink.write(uri, "t", iter([make_batch(0, 3), make_batch(3, 3)]))
    assert info.value.rows_committed == 0
    assert server.key(None, "t") not in server.tables  # nothing left, not even the table


def test_a_failure_in_append_keeps_the_existing_rows(flavour):
    sink, uri, _ = flavour
    server = FakeServer(sink.dialect)
    sink = type(sink)(connect=server.connect)
    sink.write(uri, "t", iter([make_batch(0, 2)]))
    server.fail_after_rows = server.rows_seen + 1
    with pytest.raises(WriteError):
        sink.write(uri, "t", iter([make_batch(10, 5)]), write_mode="append")
    assert [r[0] for r in server.rows("t")] == [0, 1]


def test_commit_rows_makes_rows_visible_while_the_stream_runs(flavour):
    sink, uri, _ = flavour
    seen: list[int] = []
    server = FakeServer(sink.dialect)
    server.on_row = lambda: seen.append(len(server.committed_rows("t")))
    sink = type(sink)(connect=server.connect)
    rows = sink.write(uri, "t", iter([make_batch(0, 4), make_batch(4, 4)]), commit_rows=3)
    assert rows == 8
    # rows 1-3 are written while nothing is committed, rows 4-6 after 3 are visible, ...
    assert seen[:3] == [0, 0, 0] and seen[3:6] == [3, 3, 3] and seen[6:] == [6, 6]
    assert len(server.committed_rows("t")) == 8
    assert server.events.count(("commit",)) >= 3


def test_commit_rows_failure_keeps_what_was_committed_and_says_how_many(flavour):
    sink, uri, _ = flavour
    server = FakeServer(sink.dialect, fail_after_rows=7)
    sink = type(sink)(connect=server.connect)
    with pytest.raises(WriteError, match="6 rows were committed") as info:
        sink.write(uri, "t", iter([make_batch(0, 4), make_batch(4, 6)]), commit_rows=3)
    assert info.value.rows_committed == 6
    assert len(server.rows("t")) == 6


def test_memory_stays_bounded_batches_are_consumed_as_rows_are_written(flavour):
    sink, uri, _ = flavour
    produced = 0
    consumed_rows = 0
    worst = 0

    def stream():
        nonlocal produced
        for i in range(50):
            produced += 1
            yield make_batch(i * 10, 10)

    def on_row():
        nonlocal consumed_rows, worst
        consumed_rows += 1
        worst = max(worst, produced - consumed_rows // 10)

    server = FakeServer(sink.dialect, on_row=on_row)
    sink = type(sink)(connect=server.connect)
    assert sink.write(uri, "t", stream(), batch_size=10) == 500
    assert worst <= 2  # never more than a batch or two ahead of the database


def test_values_are_converted_for_the_driver(flavour):
    sink, uri, server = flavour
    sink.write(uri, "customer", iter([make_batch(1, 1)]))
    row = server.rows("customer")[0]
    assert row[6].hour == 13  # the UTC instant
    if sink.dialect == "postgres":
        assert row[6].utcoffset() == dt.timedelta(0)
    else:
        assert row[6].tzinfo is None
    assert row[7] == b"\x01" and row[4] is False


def test_nested_values_become_json_text(flavour):
    sink, uri, server = flavour
    batch = pa.RecordBatch.from_pydict(
        {"id": [1], "tags": [["a", "b"]], "meta": [{"k": 1}]},
        schema=pa.schema(
            [
                ("id", pa.int64()),
                ("tags", pa.list_(pa.string())),
                ("meta", pa.struct([("k", pa.int64())])),
            ]
        ),
    )
    sink.write(uri, "t", iter([batch]))
    assert server.rows("t") == [(1, '["a", "b"]', '{"k": 1}')]
    ddl = next(s for s in server.statements() if s.startswith("CREATE TABLE"))
    assert "TEXT" in ddl


def test_values_never_appear_in_statements(flavour):
    sink, uri, server = flavour
    batch = pa.RecordBatch.from_pydict({"v": ["x'; DROP TABLE customer; --"]})
    sink.write(uri, "t", iter([batch]))
    assert all("DROP TABLE customer" not in s for s in server.statements("execute"))
    assert all("DROP TABLE customer" not in s for s in server.statements("executemany"))
    assert all("DROP TABLE customer" not in s for s, _ in server.copies())


def test_a_long_string_widens_the_column_and_a_very_long_one_becomes_text(flavour):
    sink, uri, server = flavour
    batch = pa.RecordBatch.from_pydict({"a": ["x" * 300], "b": ["y" * 5000], "c": ["z"]})
    sink.write(uri, "t", iter([batch]))
    ddl = next(s for s in server.statements() if s.startswith("CREATE TABLE"))
    assert "VARCHAR" not in ddl
    assert ddl.count("TEXT") == 3
    assert server.rows("t") == [("x" * 300, "y" * 5000, "z")]


def test_connection_option_is_used_as_is_and_not_closed(flavour):
    sink, uri, server = flavour
    conn = server.connect()
    sink.write(uri, "t", iter([make_batch()]), connection=conn)
    assert not conn.closed and len(server.rows("t")) == 3


def test_a_connect_failure_is_a_write_error(flavour):
    sink, uri, _ = flavour
    server = FakeServer(sink.dialect, fail_connect="no route to host")
    with pytest.raises(WriteError, match="could not connect to shape@db.example"):
        type(sink)(connect=server.connect).write(uri, "t", iter([make_batch()]))


def test_missing_driver_names_the_extra(flavour, monkeypatch):
    import sys

    sink, uri, _ = flavour
    monkeypatch.setitem(sys.modules, "psycopg", None)
    monkeypatch.setitem(sys.modules, "pymysql", None)
    with pytest.raises(ShapeError, match=r"pip install 'sqllocks-shape\[(postgres|mysql)\]'"):
        type(sink)().write(uri, "t", iter([make_batch()]))


def test_tables_written_on_several_threads_into_one_server(flavour):
    # `generate --to` writes each table on its own writer thread into one database.
    import sys
    import threading

    sys_interval = sys.getswitchinterval()
    sink, uri, server = flavour
    for i in range(300):  # a large catalogue makes each snapshot copy long
        server.tables[server.key(None, f"pre{i}")] = [(j,) for j in range(20)]
    errors: list[BaseException] = []

    def write(worker: int) -> None:
        try:
            for k in range(40):
                sink.write(uri, f"t{worker}_{k}", iter([make_batch()]))
        except BaseException as exc:  # collected and asserted on below
            errors.append(exc)

    sys.setswitchinterval(1e-6)
    try:
        threads = [threading.Thread(target=write, args=(w,)) for w in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        sys.setswitchinterval(sys_interval)
    assert errors == []
    assert all(len(server.committed_rows(f"t{w}_{k}")) == 3 for w in range(4) for k in range(40))


def test_a_whole_float_in_a_declared_integer_column_is_written_as_an_integer(flavour):
    # The engine generates some integer columns as doubles (retail's order_line.quantity); the
    # DDL follows the declared type (BIGINT), so the value must reach it as an integer: COPY's
    # text "3.0" is refused by a PostgreSQL bigint (nightly databases-e2e, shape seed).
    sink, uri, server = flavour
    batch = pa.RecordBatch.from_pydict({"id": [1, 2], "qty": pa.array([3.0, 1.0], pa.float64())})
    columns = {"qty": {"type": "integer", "nullable": False}}
    sink.write(uri, "line", iter([batch]), columns=columns)
    values = [r[1] for r in server.rows("line")]
    assert values == [3, 1] and all(type(v) is int for v in values)
    ddl = next(s for s in server.statements() if s.startswith("CREATE TABLE"))
    assert "BIGINT" in ddl.splitlines()[2]


def test_a_fraction_in_a_declared_integer_column_is_refused_naming_the_column(flavour):
    sink, uri, server = flavour
    batch = pa.RecordBatch.from_pydict({"id": [1], "qty": pa.array([2.5], pa.float64())})
    with pytest.raises(ShapeError, match="qty"):
        sink.write(uri, "line", iter([batch]), columns={"qty": {"type": "integer"}})
    assert server.key(None, "line") not in server.tables or server.rows("line") == []


def test_keys_sql_selects_the_primary_key_of_the_planned_table(monkeypatch):
    # `shape seed --mode append` reads the keys with it before writing anything.
    monkeypatch.setenv("SHAPE_POSTGRES_PASSWORD", "x")
    monkeypatch.setenv("SHAPE_MYSQL_PASSWORD", "x")
    pg = PostgresSink().plan(PG_URI, "order", {"primary_key": ["order_id"], "schema_name": "s"})
    assert PostgresSink().keys_sql(pg) == 'SELECT "order_id" FROM "s"."order"'
    my = MySqlSink().plan(MY_URI, "order", {"primary_key": ["a", "b"]})
    assert MySqlSink().keys_sql(my) == "SELECT `a`, `b` FROM `order`"
    with pytest.raises(ShapeError, match="no primary key"):
        PostgresSink().keys_sql(PostgresSink().plan(PG_URI, "t", {}))
