"""Database DDL retains native nullable UNIQUE semantics and declared widths."""

import pyarrow as pa
import pytest
from shape_databases import MySqlSink, PostgresSink


@pytest.mark.parametrize(
    "sink,uri,columns,expected",
    [
        (PostgresSink(), "postgresql://shape@localhost/db", {}, '"code" TEXT NULL'),
        (
            MySqlSink(),
            "mysql://shape@localhost/db",
            {"code": {"max_length": 64}},
            "`code` VARCHAR(64) NULL",
        ),
    ],
)
def test_nullable_unique_does_not_acquire_primary_key_nullability(sink, uri, columns, expected):
    schema = pa.schema([("code", pa.string())])
    ddl = sink.ddl(uri, "t", schema, columns=columns, unique=[["code"]])
    assert expected in ddl
    assert "UNIQUE" in ddl and "NOT NULL" not in ddl
