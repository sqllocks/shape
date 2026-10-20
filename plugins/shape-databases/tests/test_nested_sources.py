"""Actual matching Source routes read stored sink data and physical catalog types."""

import datetime as dt
from decimal import Decimal

import pyarrow as pa
import pytest
from shape_databases import MySqlSink, PostgresSink
from shape_databases.nested_readback import restore_json_column
from shape_databases.sources import MySqlSource, PostgresSource
from shape_databases.testing import FakeServer


@pytest.mark.parametrize("dialect", ["postgres", "mysql"])
def test_json_backed_sink_source_roundtrip_with_original_schema(dialect):
    server = FakeServer(dialect)
    sink = (
        PostgresSink(connect=server.connect)
        if dialect == "postgres"
        else MySqlSink(connect=server.connect)
    )
    source = (
        PostgresSource(connect=server.connect)
        if dialect == "postgres"
        else MySqlSource(connect=server.connect)
    )
    kind = pa.struct(
        [
            ("amount", pa.decimal128(12, 4)),
            ("at", pa.timestamp("us", "UTC")),
            ("entries", pa.map_(pa.int32(), pa.list_(pa.string()))),
            ("bytes", pa.binary()),
        ]
    )
    field = pa.field("payload", kind)
    expected = pa.array(
        [
            {
                "amount": Decimal("12.3400"),
                "at": dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
                "entries": [(1, ["[]", None]), (1, [])],
                "bytes": b"\x00\xff",
            },
            {"amount": None, "at": None, "entries": [], "bytes": b""},
            None,
        ],
        kind,
    )
    uri = f"{dialect}://shape@localhost/{server.default_schema}"
    assert sink.write(uri, "data", [pa.record_batch([expected], schema=pa.schema([field]))]) == 3
    physical = server.catalog[server.key(None, "data")][0][1]
    assert physical.lower() == ("jsonb" if dialect == "postgres" else "json")
    actual = pa.Table.from_batches(source.read(uri + "?table=data", batch_size=1))
    assert actual.column("payload").type == pa.string()
    restored = restore_json_column(actual.column("payload").to_pylist(), field)
    assert restored.equals(expected)


@pytest.mark.parametrize(
    "kind,values,physical",
    [
        (pa.int32(), [[1, None, 2], [], None], "INTEGER[]"),
        (pa.string(), [['a"b', "a\\b", "NULL", "", None], [], None], "TEXT[]"),
        (pa.decimal128(12, 4), [[Decimal("12.3400"), None], [], None], "NUMERIC(12,4)[]"),
        (pa.bool_(), [[True, False, None], [], None], "BOOLEAN[]"),
        (pa.binary(), [[b"\x00\xff", b"", None], [], None], "BYTEA[]"),
        (
            pa.timestamp("us", "UTC"),
            [[dt.datetime(2026, 1, 2, tzinfo=dt.UTC), None], [], None],
            "TIMESTAMPTZ[]",
        ),
    ],
)
def test_postgres_scalar_arrays_read_native_values_from_stored_copy_literals(
    kind, values, physical
):
    server = FakeServer("postgres", decode_arrays=True)
    sink = PostgresSink(connect=server.connect)
    source = PostgresSource(connect=server.connect)
    uri = "postgres://shape@localhost/public"
    expected = pa.table({"payload": pa.array(values, pa.list_(kind))})
    assert sink.write(uri, "data", expected.to_batches()) == 3
    assert server.catalog[server.key(None, "data")][0][1] == physical
    assert isinstance(server.rows("data")[0][0], str)  # stored COPY data, not expected values
    actual = pa.Table.from_batches(source.read(uri + "?table=data", batch_size=1))
    assert actual.column("payload").type == expected.column("payload").type
    assert actual.to_pylist() == expected.to_pylist()
