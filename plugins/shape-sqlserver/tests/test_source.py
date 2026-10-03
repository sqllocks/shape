"""The mssql:// source and the profile-db command, with the in-memory server."""

import datetime as dt
import json
import struct
from decimal import Decimal

import pyarrow as pa
import pytest
from shape_sqlserver import ProfileDbCommand, SqlServerError, SqlServerSource
from shape_sqlserver.source import normalize_value, parse_uri
from shape_sqlserver.testing import FakeColumn, FakeConnection, FakeTable, scenario

from shape.plugins import kit

pytestmark = pytest.mark.contract

URI = "mssql://db.example.test:1433/shop?schema=dbo&table=orders"


def make_source(name="retail"):
    return SqlServerSource(connection_factory=lambda: scenario(name))


def test_kit_conformance():
    kit.check_source(make_source(), URI, unrelated_uri="file:///data/x.csv")


def test_module_declares_the_api():
    import shape_sqlserver

    assert kit.check_module_api(shape_sqlserver) == "1.0"


def test_can_open_only_mssql_uris_with_a_table():
    src = make_source()
    assert src.can_open(URI)
    assert not src.can_open("mssql://db.example.test/shop")  # no table
    assert not src.can_open("postgres://h/db?table=t")
    assert not src.can_open("/tmp/orders.csv")


def test_parse_uri():
    assert parse_uri(URI) == ("db.example.test", 1433, "shop", "dbo", "orders")
    assert parse_uri("mssql:///?table=t&schema=s") == ("", None, None, "s", "t")
    with pytest.raises(SqlServerError, match="no table"):
        parse_uri("mssql://h/db")
    with pytest.raises(SqlServerError, match="not an mssql"):
        parse_uri("http://h/db?table=t")


def test_schema_comes_from_the_catalog():
    schema = make_source().schema(URI)
    assert schema.names == [
        "order_id",
        "customer_id",
        "order_date",
        "status",
        "total",
        "ship_time",
        "notes",
    ]
    assert schema.field("order_id").type == pa.int64()
    assert schema.field("order_id").nullable is False
    assert schema.field("total").type == pa.decimal128(10, 2)
    assert schema.field("order_date").type == pa.date32()
    assert schema.field("ship_time").type == pa.time64("us")
    assert schema.field("status").type == pa.string()


def test_read_returns_every_row_in_primary_key_order_and_batches():
    conn = scenario("retail")
    src = SqlServerSource()
    batches = list(src.read(URI, connection=conn, batch_size=1500))
    assert [b.num_rows for b in batches] == [1500, 1500, 1000]
    table = pa.Table.from_batches(batches)
    source_rows = next(t for t in conn.tables if t.name == "orders").rows
    ids = table.column("order_id").to_pylist()
    assert ids == sorted(r[0] for r in source_rows)
    assert table.column("total").to_pylist()[0] == min(source_rows)[4]
    assert any("ORDER BY [order_id]" in s for s in conn.statements)
    assert conn.closed is False  # a passed connection is not ours to close


def test_read_values_survive_the_round_trip():
    conn = scenario("retail")
    src = SqlServerSource()
    table = pa.Table.from_batches(list(src.read("mssql:///?table=customer", connection=conn)))
    rows = sorted(next(t for t in conn.tables if t.name == "customer").rows)
    assert table.num_rows == len(rows)
    first = table.slice(0, 1).to_pylist()[0]
    assert first["name"] == rows[0][1]
    assert first["balance"] == rows[0][4]
    assert first["created_at"] == rows[0][6]
    assert first["guid"] == rows[0][11]


def test_an_empty_table_yields_no_batches_but_has_a_schema():
    conn = scenario("retail")
    uri = "mssql:///?table=audit_log"
    assert list(SqlServerSource().read(uri, connection=conn)) == []
    assert SqlServerSource().schema(uri, connection=conn).names == ["id", "event", "at"]


def test_a_missing_table_is_a_clear_error():
    with pytest.raises(SqlServerError, match=r"dbo\.nope not found"):
        make_source().schema("mssql:///?table=nope")
    with pytest.raises(SqlServerError, match="not found"):
        list(make_source().read("mssql:///?table=nope"))


def test_an_owned_connection_is_closed_after_the_read():
    conns = []

    def factory():
        conns.append(scenario("retail"))
        return conns[-1]

    list(SqlServerSource(connection_factory=factory).read("mssql:///?table=config"))
    assert conns[0].closed is True


def test_no_server_in_the_uri_and_no_connection_is_an_error():
    with pytest.raises(SqlServerError, match="names no server"):
        SqlServerSource().schema("mssql:///?table=t")


def test_batch_size_must_be_positive():
    with pytest.raises(SqlServerError, match="batch_size"):
        list(make_source().read("mssql:///?table=config", batch_size=0))


def test_uri_options_build_a_secret_free_connection_string(monkeypatch):
    seen = {}

    def fake_connect(text, creds):
        seen["text"], seen["creds"] = text, creds
        return scenario("retail")

    monkeypatch.setattr("shape_sqlserver.source.connect", fake_connect)
    SqlServerSource().schema(URI, user="sa", password="pw")
    assert "Server=db.example.test,1433" in seen["text"] and "Database=shop" in seen["text"]
    assert seen["creds"].method == "sql"
    SqlServerSource().schema(URI, auth="msi")
    assert seen["creds"].method == "msi" and "PWD" not in seen["text"]


def test_datetimeoffset_bytes_become_utc_timestamps():
    raw = struct.pack("<6hI2h", 2024, 3, 5, 10, 30, 15, 123_456_000, 2, 0)
    assert normalize_value(raw, "datetimeoffset") == dt.datetime(
        2024, 3, 5, 8, 30, 15, 123456, tzinfo=dt.UTC
    )
    assert normalize_value(None, "datetimeoffset") is None
    aware = dt.datetime(2024, 1, 1, 1, tzinfo=dt.timezone(dt.timedelta(hours=1)))
    assert normalize_value(aware, "datetimeoffset") == dt.datetime(2024, 1, 1, tzinfo=dt.UTC)
    assert normalize_value(Decimal("1.5"), "decimal") == Decimal("1.5")
    assert normalize_value(bytearray(b"ab"), "varbinary") == b"ab"


def test_wide_type_round_trip():
    cols = [
        FakeColumn("a", "tinyint", False, precision=3),
        FakeColumn("b", "bit"),
        FakeColumn("c", "real"),
        FakeColumn("d", "money", precision=19, scale=4),
        FakeColumn("e", "varbinary", max_length=8),
        FakeColumn("f", "datetime2", scale=7),
        FakeColumn("g", "datetimeoffset", scale=7),
    ]
    off = struct.pack("<6hI2h", 2024, 1, 1, 0, 0, 0, 0, 0, 0)
    row = (7, True, 1.5, Decimal("12.3400"), b"\x01\x02", dt.datetime(2024, 1, 2, 3, 4, 5), off)
    conn = FakeConnection([FakeTable("wide", cols, [row, (8, None, None, None, None, None, None)])])
    table = pa.Table.from_batches(
        list(SqlServerSource().read("mssql:///?table=wide", connection=conn))
    )
    got = table.to_pylist()
    assert got[0]["a"] == 7 and got[0]["b"] is True and got[0]["d"] == Decimal("12.3400")
    assert got[0]["g"] == dt.datetime(2024, 1, 1, tzinfo=dt.UTC)
    assert got[1] == {k: None for k in "bcdefg"} | {"a": 8}


# --- the command -----------------------------------------------------------------------


def test_command_conformance(monkeypatch, tmp_path):
    monkeypatch.setattr("shape_sqlserver.profiler.connect", lambda text, creds: scenario("retail"))
    out = tmp_path / "db.shape"
    kit.check_command(
        ProfileDbCommand(), ["--connection-string", "Server=x", "--schema", "dbo", "-o", str(out)]
    )
    assert out.exists()


def test_command_profiles_and_writes_artifact_and_summary(monkeypatch, tmp_path, capsys):
    seen = {}

    def fake_connect(text, creds):
        seen.update(text=text, creds=creds)
        return scenario("retail")

    monkeypatch.setattr("shape_sqlserver.profiler.connect", fake_connect)
    monkeypatch.setenv("SHAPE_SQLSERVER_CLIENT_SECRET", "from-env")
    import argparse

    cmd = ProfileDbCommand()
    parser = argparse.ArgumentParser()
    cmd.configure(parser)
    out, summary = tmp_path / "p.shape", tmp_path / "s.json"
    args = parser.parse_args(
        [
            "--server",
            "srv",
            "--database",
            "shop",
            "--auth",
            "spn",
            "--tenant-id",
            "t",
            "--client-id",
            "c",
            "--tables",
            "orders, customer",
            "--sample-rows",
            "50",
            "-o",
            str(out),
            "--json",
            str(summary),
        ]
    )
    assert cmd.run(args) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["tables"] == 2 and printed["relationships"] == 1
    assert len(printed["shape_content_id"]) == 64
    assert seen["creds"].method == "spn" and seen["creds"].client_secret == "from-env"
    assert "Server=srv" in seen["text"] and "Database=shop" in seen["text"]
    assert json.loads(summary.read_text())["tables"].keys() == {"orders", "customer"}
    import shape

    assert shape.load(out).name == "shop"


def test_command_connection_string_from_environment(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(
        "shape_sqlserver.profiler.connect",
        lambda text, creds: seen.setdefault("text", text) and scenario("retail"),
    )
    monkeypatch.setenv("SHAPE_SQLSERVER_CONNECTION_STRING", "Server=env-server;UID=u;PWD=p")
    import argparse

    cmd = ProfileDbCommand()
    parser = argparse.ArgumentParser()
    cmd.configure(parser)
    assert cmd.run(parser.parse_args(["--auth", "sql", "-o", str(tmp_path / "x.shape")])) == 0
    assert seen["text"] == "Server=env-server;UID=u;PWD=p"


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["-o", "x.shape"], "--connection-string"),
        (["--server", "s"], "needs -o"),
        (["--server", "s", "-o", "x.shape", "--sample-rows", "-1"], "negative"),
    ],
)
def test_command_input_errors_exit_2_with_a_message(argv, message, capsys, monkeypatch):
    monkeypatch.delenv("SHAPE_SQLSERVER_CONNECTION_STRING", raising=False)
    import argparse

    cmd = ProfileDbCommand()
    parser = argparse.ArgumentParser()
    cmd.configure(parser)
    assert cmd.run(parser.parse_args(argv)) == 2
    assert message in capsys.readouterr().err


def test_command_errors_never_print_the_password(monkeypatch, capsys, tmp_path):
    def boom(text, creds):
        raise SqlServerError("could not connect: UID=sa;PWD=hunter2")

    monkeypatch.setattr("shape_sqlserver.profiler.connect", boom)
    import argparse

    cmd = ProfileDbCommand()
    parser = argparse.ArgumentParser()
    cmd.configure(parser)
    args = parser.parse_args(
        ["--connection-string", "Server=s;PWD=hunter2", "--auth", "sql", "-o", str(tmp_path / "x")]
    )
    assert cmd.run(args) == 2
    assert "hunter2" not in capsys.readouterr().err


def test_alias_type_columns_are_read_and_profiled_as_their_base_type():
    # Issue #340: the catalog named an alias type (CREATE TYPE Amount FROM int) by its alias, so
    # the column was typed as text, failed to read and was profiled as a string.
    def conn():
        return FakeConnection(
            [
                FakeTable(
                    "t",
                    [
                        FakeColumn("id", "int"),
                        FakeColumn("amt", "decimal", precision=10, scale=2, alias="Amount"),
                        FakeColumn("flag", "bit", alias="Flag"),
                    ],
                    [(1, Decimal("5.25"), True), (2, Decimal("6.50"), False)],
                    primary_key=("id",),
                )
            ]
        )

    src = SqlServerSource()
    schema = src.schema("mssql:///?table=t", connection=conn())
    assert schema.field("amt").type == pa.decimal128(10, 2)
    assert schema.field("flag").type == pa.bool_()
    (batch,) = list(src.read("mssql:///?table=t", connection=conn()))
    assert batch.column("amt").to_pylist() == [Decimal("5.25"), Decimal("6.50")]
    assert batch.column("flag").to_pylist() == [True, False]

    from shape_sqlserver import profile_database

    columns = profile_database(connection=conn()).summary()["tables"]["t"]["columns"]
    assert columns["amt"]["dtype"] == "float"
    assert columns["amt"]["min"] == 5.25
    assert columns["flag"]["dtype"] == "boolean"
