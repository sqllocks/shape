"""mssql:// with connection_string: the URI's write options and schema still apply (#443)."""

from __future__ import annotations

import pyarrow as pa
from shape_fabric.sinks import SqlServerSink
from shape_fabric.testing import FakeSqlServer

CS = "Driver={ODBC Driver 18 for SQL Server};Server=h;Database=d"


def test_the_uri_query_applies_with_a_connection_string() -> None:
    server = FakeSqlServer()
    sink = SqlServerSink(connect=server.connect)
    batch = pa.record_batch({"id": pa.array([1])})
    assert sink.write("mssql://h/d?schema=sales", "t", [batch], connection_string=CS) == 1
    uri = "mssql://h/d?write_mode=append&schema=sales&batch_size=1"
    assert sink.write(uri, "t", [batch], connection_string=CS) == 1
    assert sorted(server.tables) == [("sales", "t")]


def test_an_option_still_beats_the_uri() -> None:
    server = FakeSqlServer()
    sink = SqlServerSink(connect=server.connect)
    batch = pa.record_batch({"id": pa.array([1])})
    sink.write("mssql://h/d", "t", [batch], connection_string=CS)
    uri = "mssql://h/d?write_mode=create"
    assert sink.write(uri, "t", [batch], connection_string=CS, write_mode="append") == 1
