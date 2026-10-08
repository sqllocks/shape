"""SqlDatabaseWriter: write modes, transactions, SQL safety, types, connections."""

import datetime as dt
import sys
import types
from decimal import Decimal

import pyarrow as pa
import pytest
from shape_fabric import SqlDatabaseWriter, WriteError, _tsql
from shape_fabric.testing import FakeSqlServer, sample_schema

from shape.errors import ShapeError

CS = (
    "Driver={ODBC Driver 18 for SQL Server};Server=db.example.test;Database=d;"
    "UID=u;PWD=hunter2hunter2"
)


def writer(server, **kw):
    return SqlDatabaseWriter(CS, connect=server.connect, **kw)


def test_default_mode_creates_and_writes_with_parameters(batches):
    server = FakeSqlServer()
    assert writer(server).write_table("customer", batches) == 7
    rows = server.rows("dbo", "customer")
    assert len(rows) == 7
    # nanosecond timestamps cut to microseconds, time zones to UTC naive, NaN to NULL
    assert rows[0][7] == dt.datetime(2026, 1, 1, 12, 0, 0, 123456)
    assert rows[0][8] == dt.datetime(2026, 1, 1, 12, 0, 0, 123456)  # the instant, in UTC
    assert rows[0][4] is None and rows[1][4] == pytest.approx(1 / 3)
    assert rows[1][3] == Decimal("0.25") and rows[0][2] == "a" and rows[3][2] is None


def test_an_existing_table_is_never_touched_by_the_default_mode(batches):
    server = FakeSqlServer()
    w = writer(server)
    w.write_table("customer", batches)
    before = server.rows("dbo", "customer")
    with pytest.raises(WriteError, match="already exists"):
        w.write_tables({"customer": batches})
    assert server.rows("dbo", "customer") == before
    assert not any("DROP" in s or "TRUNCATE" in s for s in server.statements)


def test_append_truncate_and_replace(batches):
    server = FakeSqlServer()
    w = writer(server)
    w.write_table("t", batches)
    w.write_table("t", batches, write_mode="append")
    assert len(server.rows("dbo", "t")) == 14
    w.write_table("t", batches[:1], write_mode="truncate")
    assert len(server.rows("dbo", "t")) == 4
    w.write_table("t", batches[1:], write_mode="replace")
    assert len(server.rows("dbo", "t")) == 3
    w.write_table("fresh", batches, write_mode="append")  # append creates a missing table
    assert len(server.rows("dbo", "fresh")) == 7


def test_unknown_mode_and_batch_size_are_refused(batches):
    w = writer(FakeSqlServer())
    with pytest.raises(ShapeError, match="unknown write mode"):
        w.write_table("t", batches, write_mode="create_insert")
    for bad in (0, -1, True, 2.5):
        with pytest.raises(ShapeError, match="batch_size"):
            w.write_table("t", batches, batch_size=bad)


def test_a_failed_insert_rolls_back_and_drops_the_table_it_created(batches):
    server = FakeSqlServer()

    def fail(sql, params):
        if sql.startswith("INSERT") and len(server.statements) > 6:
            raise RuntimeError("disk full")

    w = writer(server)
    w.write_table("keep", batches)
    server.fail = None
    calls = {"n": 0}

    def explode(sql, params):
        if sql.startswith("INSERT"):
            calls["n"] += 1

    # fail on the executemany of the second 3-row piece
    original = server.connect

    def connect(cs, credential=None, **kw):
        conn = original(cs, credential)
        cursor_cls = type(conn.cursor())
        real = cursor_cls.executemany
        state = {"n": 0}

        def executemany(self, sql, rows):
            state["n"] += 1
            if state["n"] == 2:
                raise RuntimeError("disk full")
            return real(self, sql, rows)

        cursor_cls.executemany = executemany
        return conn

    w2 = SqlDatabaseWriter(CS, connect=connect)
    with pytest.raises(WriteError, match="disk full"):
        w2.write_table("new", batches, batch_size=3)
    assert ("dbo", "new") not in server.tables  # created by this call, so dropped again
    assert len(server.rows("dbo", "keep")) == 7


def test_a_failed_append_rolls_back_only_its_own_rows(batches):
    server = FakeSqlServer()
    w = writer(server)
    w.write_table("t", batches)
    bad = [pa.RecordBatch.from_arrays([pa.array([1])], names=["x"])]
    with pytest.raises(ShapeError, match="a batch has columns"):
        w.write_table("t", [*batches, *bad], write_mode="append")
    assert len(server.rows("dbo", "t")) == 7  # the append's rows were rolled back
    assert ("dbo", "t") in server.tables  # and the existing table was not dropped


def _three_then_failure():
    yield pa.RecordBatch.from_arrays([pa.array([1, 2, 3])], names=["id"])
    raise RuntimeError("source died")


@pytest.mark.parametrize("mode", ["truncate", "replace"])
def test_a_failed_truncate_or_replace_keeps_the_old_rows(mode):
    # #429: the TRUNCATE or DROP is part of the write's one transaction, so a failure rolls
    # it back with the new rows
    server = FakeSqlServer()
    w = writer(server)
    w.write_table("t", [pa.RecordBatch.from_arrays([pa.array([7, 8, 9])], names=["id"])])
    before = server.rows("dbo", "t")
    with pytest.raises(WriteError, match="source died"):
        w.write_table("t", _three_then_failure(), write_mode=mode)
    assert server.rows("dbo", "t") == before
    assert len(before) == 3


@pytest.mark.parametrize("mode", ["truncate", "replace"])
def test_a_truncate_or_replace_commits_once_with_its_rows(mode, batches):
    server = FakeSqlServer()
    w = writer(server)
    w.write_table("t", batches)
    commits = []
    real = server.snapshot
    server.snapshot = lambda: (commits.append(list(server.statements)), real())[1]
    w.write_table("t", batches[:1], write_mode=mode)
    assert len(commits) == 1  # nothing is committed before the rows are in
    assert len(server.rows("dbo", "t")) == 4


def test_a_failed_replace_of_a_missing_table_leaves_no_table():
    server = FakeSqlServer()
    with pytest.raises(WriteError, match="source died"):
        writer(server).write_table("t", _three_then_failure(), write_mode="replace")
    assert ("dbo", "t") not in server.tables


def test_with_commit_rows_a_failed_replace_keeps_the_old_rows_until_the_first_commit():
    server = FakeSqlServer()
    w = writer(server)
    w.write_table("t", [pa.RecordBatch.from_arrays([pa.array([7, 8, 9])], names=["id"])])
    with pytest.raises(WriteError, match="source died"):
        w.write_table("t", _three_then_failure(), write_mode="replace", commit_rows=10)
    assert server.rows("dbo", "t") == [(7,), (8,), (9,)]
    # once a chunk is committed, the replace is committed with it and the chunk stays
    with pytest.raises(WriteError, match="source died"):
        w.write_table("t", _three_then_failure(), write_mode="replace", commit_rows=2, batch_size=2)
    assert server.rows("dbo", "t") == [(1,), (2,)]


@pytest.mark.parametrize("mode", ["truncate", "replace"])
def test_with_constraints_disabled_a_failed_truncate_or_replace_keeps_the_old_rows(mode):
    # #429 with W2-10's constraints="disable": turning checking off must not commit the
    # TRUNCATE or DROP before the rows are in
    server = FakeSqlServer()
    w = writer(server)
    w.write_table("t", [pa.RecordBatch.from_arrays([pa.array([7, 8, 9])], names=["id"])])
    with pytest.raises(WriteError, match="source died"):
        w.write_table("t", _three_then_failure(), write_mode=mode, constraints="disable")
    assert server.rows("dbo", "t") == [(7,), (8,), (9,)]


def test_with_constraints_disabled_a_replace_has_nothing_to_disable(batches):
    # the replaced table is new, as one created by the run is: no ALTER TABLE
    server = FakeSqlServer()
    w = writer(server)
    w.write_table("t", batches)
    w.write_table("t", batches[:1], write_mode="replace", constraints="disable")
    assert not [s for s in server.statements if s.startswith("ALTER TABLE")]
    assert len(server.rows("dbo", "t")) == 4


def test_write_tables_stops_at_the_first_failure_and_reports_progress(batches):
    server = FakeSqlServer()
    w = writer(server)
    w.write_table("b", batches)
    with pytest.raises(WriteError) as info:
        w.write_tables({"a": batches, "b": batches, "c": batches})
    assert info.value.result.per_table == {"a": 7}
    assert ("dbo", "c") not in server.tables


def test_sql_safety_names_are_quoted_and_values_are_parameters():
    server = FakeSqlServer()
    nasty_table = "t]; DROP TABLE users; --"
    nasty_col = "c]] ) VALUES (1); DROP TABLE users;--"
    batch = pa.RecordBatch.from_arrays(
        [pa.array(["x'); DROP TABLE users;--"]), pa.array([1])], names=[nasty_col, "ok"]
    )
    w = writer(server, schema_name="s]; DROP SCHEMA x;--")
    assert w.write_table(nasty_table, [batch]) == 1
    assert ("s]; DROP SCHEMA x;--", nasty_table) in server.tables
    # every identifier is bracket-quoted with each ] doubled, so a name can never end early
    insert = next(s for s in server.statements if s.startswith("INSERT"))
    assert insert == (
        "INSERT INTO [s]]; DROP SCHEMA x;--].[t]]; DROP TABLE users; --] "
        "([c]]]] ) VALUES (1); DROP TABLE users;--], [ok]) VALUES (?, ?)"
    )
    # the value went to the driver as data
    assert server.rows("s]; DROP SCHEMA x;--", nasty_table) == [("x'); DROP TABLE users;--", 1)]


@pytest.mark.parametrize("name", ["", "a\x00b", "x" * 129])
def test_bad_names_are_refused_before_anything_runs(name, batches):
    server = FakeSqlServer()
    with pytest.raises(ShapeError):
        writer(server).write_table(name, batches)
    assert server.statements == []


def test_time_zones_are_converted_to_the_utc_instant():
    batch = pa.RecordBatch.from_arrays(
        [
            pa.array([dt.datetime(2026, 1, 1)], pa.timestamp("us")).cast(
                pa.timestamp("us", "Asia/Tokyo")
            )
        ],
        names=["t"],
    )
    server = FakeSqlServer()
    writer(server).write_table("tz", [batch])
    assert server.rows("dbo", "tz") == [(dt.datetime(2026, 1, 1),)]  # not +09:00


def test_create_ddl_types_for_sql_database_and_warehouse():
    schema = sample_schema().append(pa.field("raw", pa.binary())).append(pa.field("u", pa.uint8()))
    ddl = writer(FakeSqlServer()).create_ddl("t", schema)
    assert "[name] NVARCHAR(MAX) NULL" in ddl and "[seen] DATETIME2(6) NULL" in ddl
    assert "[balance] DECIMAL(12,2) NULL" in ddl and "[score] FLOAT NULL" in ddl
    assert "[active] BIT NULL" in ddl and "[born] DATE NULL" in ddl
    assert "[segment] NVARCHAR(MAX) NULL" in ddl and "[raw] VARBINARY(MAX) NULL" in ddl
    assert "[u] TINYINT NULL" in ddl
    wh = SqlDatabaseWriter(
        "Server=x.datawarehouse.fabric.microsoft.com;Database=w;UID=u;PWD=p",
        connect=FakeSqlServer().connect,
    )
    assert wh.db.warehouse
    ddl = wh.create_ddl("t", schema, primary_key=["id"])
    assert "[name] VARCHAR(8000) NULL" in ddl and "[raw] VARBINARY(8000) NULL" in ddl
    assert "[u] SMALLINT NULL" in ddl  # the Warehouse has no TINYINT
    assert "NVARCHAR" not in ddl
    assert "CONSTRAINT [PK_t] PRIMARY KEY NONCLUSTERED ([id]) NOT ENFORCED" in ddl
    assert "[id] BIGINT NOT NULL" in ddl


def test_declared_lengths_keys_and_unsupported_types():
    schema = pa.schema([("code", pa.string()), ("k", pa.string()), ("n", pa.list_(pa.int8()))])
    w = writer(FakeSqlServer())
    with pytest.raises(ShapeError, match="column 'n'"):
        w.create_ddl("t", schema)
    ok = pa.schema([("code", pa.string()), ("k", pa.string())])
    ddl = w.create_ddl(
        "t", ok, columns={"code": {"max_length": 2, "nullable": False}}, primary_key=["k"]
    )
    assert "[code] NVARCHAR(2) NOT NULL" in ddl and "[k] NVARCHAR(450) NOT NULL" in ddl
    with pytest.raises(ShapeError, match="primary key column"):
        w.create_ddl("t", ok, primary_key=["nope"])


def test_a_value_longer_than_its_declared_length_fails_loudly_not_truncated():
    server = FakeSqlServer()
    batch = pa.RecordBatch.from_arrays([pa.array(["ok", "toolong"])], names=["code"])
    with pytest.raises(WriteError, match="truncated"):
        writer(server).write_table("t", [batch], columns={"code": {"max_length": 3}}, batch_size=1)


def test_fast_executemany_is_used_only_when_row_zero_is_the_widest():
    server = FakeSqlServer()
    flags = []
    original = server.connect

    def connect(cs, credential=None, **kw):
        conn = original(cs, credential)
        cls = type(conn.cursor())

        class Spy(cls):  # type: ignore[valid-type, misc]
            def executemany(self, sql, rows):
                flags.append(self.fast_executemany)
                return super().executemany(sql, rows)

        conn.cursor = lambda: Spy(server)
        return conn

    w = SqlDatabaseWriter(CS, connect=connect)
    widest_first = pa.RecordBatch.from_arrays([pa.array(["abcdef", "a", "bb"])], names=["s"])
    widest_later = pa.RecordBatch.from_arrays([pa.array(["a", "abcdef", "bb"])], names=["s"])
    w.write_table("one", [widest_first])
    w.write_table("two", [widest_later])
    assert flags == [True, False]
    assert [r[0] for r in server.rows("dbo", "two")] == [
        "a",
        "abcdef",
        "bb",
    ]  # nothing cut or reordered


def test_schema_is_created_when_missing_and_an_empty_table_needs_a_schema(batches):
    server = FakeSqlServer()
    w = writer(server, schema_name="gen")
    w.write_table("t", batches)
    assert "gen" in server.schemas
    with pytest.raises(ShapeError, match="no batches and no schema"):
        w.write_table("e", [])
    assert w.write_table("e", [], schema=sample_schema()) == 0
    assert ("gen", "e") in server.tables


def test_an_open_connection_is_used_and_never_closed(batches):
    server = FakeSqlServer()
    conn = server.connect("x")
    w = SqlDatabaseWriter(connection=conn)
    w.write_table("t", batches)
    w.close()
    assert not conn.closed and len(server.rows("dbo", "t")) == 7
    assert server.connections == 1


def test_the_destination_never_shows_the_password():
    w = writer(FakeSqlServer())
    assert "hunter2" not in w.destination and "PWD=***" in w.destination


def test_ado_net_connection_strings_become_odbc():
    cs = (
        "Data Source=tcp:abc.datawarehouse.fabric.microsoft.com,1433;Initial Catalog=wh;"
        'User ID=app;Password="p;w=1";Encrypt=True;Connect Timeout=45;'
        "Authentication=Active Directory Default"
    )
    out = _tsql.normalize_connection_string(cs)
    assert out.startswith(
        "Driver={ODBC Driver 18 for SQL Server};Server=abc.datawarehouse.fabric.microsoft.com,1433;"
    )
    assert "Database=wh" in out and "UID=app" in out and "PWD={p;w=1}" in out
    assert (
        "Encrypt=yes" in out
        and "Connection Timeout=45" in out
        and "Authentication=ActiveDirectoryDefault" in out
    )
    assert _tsql.is_warehouse(out)
    assert _tsql.normalize_connection_string(CS) == CS  # ODBC strings pass through
    with pytest.raises(ShapeError):
        _tsql.normalize_connection_string("Initial Catalog=x")


def _fake_pyodbc(monkeypatch, fail_times=0):
    calls = []

    class Error(Exception):
        pass

    def connect(cs, **kw):
        calls.append((cs, kw))
        if len(calls) <= fail_times:
            raise Error("login failed PWD=hunter2hunter2")
        return object()

    mod = types.SimpleNamespace(connect=connect, Error=Error)
    monkeypatch.setitem(sys.modules, "pyodbc", mod)
    return calls


def test_a_credential_gives_the_driver_an_access_token(monkeypatch):
    calls = _fake_pyodbc(monkeypatch)
    seen = []

    def credential(scope):
        seen.append(scope)
        return "tok-123"

    _tsql.connect("Driver={x};Server=s;Database=d", credential)
    assert seen == ["https://database.windows.net/.default"]
    cs, kw = calls[0]
    packed = kw["attrs_before"][1256]
    assert packed == (len("tok-123") * 2).to_bytes(4, "little") + "tok-123".encode("utf-16-le")
    assert kw["autocommit"] is False


def test_credential_and_login_in_the_string_conflict(monkeypatch):
    _fake_pyodbc(monkeypatch)
    with pytest.raises(ShapeError, match="must not hold UID, PWD or Authentication"):
        _tsql.connect("Driver={x};Server=s;UID=u;PWD=p", lambda scope: "t")


def test_sign_in_is_retried_and_errors_hide_the_password(monkeypatch):
    calls = _fake_pyodbc(monkeypatch, fail_times=2)
    _tsql.connect("Driver={x};Server=s", lambda scope: "t", retry_delay=0)
    assert len(calls) == 3
    _fake_pyodbc(monkeypatch, fail_times=9)
    with pytest.raises(ShapeError) as info:
        _tsql.connect("Driver={x};Server=s", lambda scope: "t", retry_delay=0, retries=2)
    assert "hunter2" not in str(info.value)
    _fake_pyodbc(monkeypatch, fail_times=9)
    with pytest.raises(ShapeError) as info:
        _tsql.connect("Driver={x};Server=s;UID=u;PWD=hunter2hunter2")
    assert "hunter2" not in str(info.value)


def test_a_batch_with_other_columns_is_refused_not_mapped_by_position():
    server = FakeSqlServer()
    a = pa.RecordBatch.from_arrays([pa.array([1]), pa.array(["x"])], names=["id", "s"])
    reordered = pa.RecordBatch.from_arrays([pa.array(["y"]), pa.array([2])], names=["s", "id"])
    with pytest.raises(ShapeError, match="a batch has columns"):
        writer(server).write_table("t", [a, reordered])
    assert ("dbo", "t") not in server.tables  # rolled back and dropped: nothing half written
