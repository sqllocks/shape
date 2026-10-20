"""SHAPE-SINK-003: recursive SQL DDL, value encodings and preconnection refusals."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import pyarrow as pa
import pytest
from shape_databases import DatabricksSink, MySqlSink, PostgresSink, SnowflakeSink, _cloud, _sql
from shape_databases.databricks import binding
from shape_databases.errors import WriteError
from shape_databases.testing import FakeDatabricks, FakeServer, FakeSnowflake

from shape.errors import ShapeError


def sink_fixture(dialect):
    if dialect == "postgres":
        server = FakeServer(dialect)
        return PostgresSink(connect=server.connect), "postgresql://user@localhost/db", server
    if dialect == "mysql":
        server = FakeServer(dialect)
        return MySqlSink(connect=server.connect), "mysql://user@localhost/db", server
    if dialect == "snowflake":
        server = FakeSnowflake()
        return SnowflakeSink(connect=server.connect), "snowflake://user@account/db/PUBLIC", server
    server = FakeDatabricks()
    return DatabricksSink(connect=server.connect), "databricks://host/warehouse?schema=demo", server


def test_databricks_submicrosecond_timestamp_refused_before_connection():
    sink, uri, server = sink_fixture("databricks")
    values = pa.array([1767323045123456789], pa.int64()).cast(pa.timestamp("ns", "UTC"))
    batch = pa.record_batch([pa.ListArray.from_arrays([0, 1], values)], names=["payload"])
    with pytest.raises(ShapeError, match="payload.*precision.*nested=json"):
        sink.write(uri, "t", [batch])
    assert not server.events


def test_databricks_later_submicrosecond_timestamp_keeps_prior_committed_rows():
    sink, uri, server = sink_fixture("databricks")

    def batch(value):
        items = pa.array([value], pa.int64()).cast(pa.timestamp("ns", "UTC"))
        return pa.record_batch([pa.ListArray.from_arrays([0, 1], items)], names=["payload"])

    with pytest.raises(WriteError, match="payload.*precision.*nested=json") as error:
        sink.write(
            uri, "t", [batch(1767323045123456000), batch(1767323045123456789)], commit_rows=1
        )
    assert error.value.rows_committed == 1
    assert len(server.rows("t")) == 1


@pytest.mark.parametrize("dialect", ["postgres", "mysql"])
@pytest.mark.parametrize("shape", ["list", "struct", "map"])
def test_native_json_nanosecond_time_shape_refused_before_pylist_or_connection(dialect, shape):
    sink, uri, server = sink_fixture(dialect)
    kind = {
        "list": pa.list_(pa.time64("ns")),
        "struct": pa.struct([("time", pa.time64("ns"))]),
        "map": pa.map_(pa.string(), pa.time64("ns")),
    }[shape]
    with pytest.raises(ShapeError, match="payload.*nanosecond.*exact.*string"):
        sink.write(uri, "t", [], schema=pa.schema([("payload", kind)]))
    assert not server.events


@pytest.mark.conformance
@pytest.mark.parametrize(
    "kind,expected",
    [
        (pa.list_(pa.string()), "TEXT[]"),
        (pa.list_(pa.int8()), "SMALLINT[]"),
        (pa.list_(pa.int16()), "SMALLINT[]"),
        (pa.list_(pa.int32()), "INTEGER[]"),
        (pa.list_(pa.int64()), "BIGINT[]"),
        (pa.list_(pa.uint64()), "NUMERIC(20,0)[]"),
        (pa.list_(pa.decimal128(12, 4)), "NUMERIC(12,4)[]"),
        (pa.list_(pa.timestamp("us", "UTC")), "TIMESTAMPTZ[]"),
        (pa.struct([("x", pa.int32())]), "JSONB"),
        (pa.list_(pa.struct([("x", pa.int32())])), "JSONB"),
        (pa.map_(pa.string(), pa.int32()), "JSONB"),
    ],
)
def test_postgres_native_nested_columns(kind, expected):
    ddl = _sql.create_table_sql(None, "t", pa.schema([("payload", kind)]), "postgres")
    assert f'"payload" {expected} NULL' in ddl


@pytest.mark.parametrize(
    "kind",
    [pa.list_(pa.int32()), pa.struct([("x", pa.int32())]), pa.map_(pa.string(), pa.int32())],
)
def test_mysql_every_nested_shape_is_native_json(kind):
    ddl = _sql.create_table_sql(None, "t", pa.schema([("payload", kind)]), "mysql")
    assert "`payload` JSON NULL" in ddl


@pytest.mark.parametrize(
    "kind,expected",
    [
        (pa.list_(pa.int8()), "ARRAY<TINYINT>"),
        (pa.list_(pa.int16()), "ARRAY<SMALLINT>"),
        (pa.list_(pa.int32()), "ARRAY<INT>"),
        (pa.list_(pa.int64()), "ARRAY<BIGINT>"),
        (pa.struct([("x", pa.int32())]), "STRUCT<`x`: INT>"),
        (pa.map_(pa.string(), pa.list_(pa.decimal128(12, 4))), "MAP<STRING, ARRAY<DECIMAL(12,4)>>"),
    ],
)
def test_databricks_native_types_are_recursive(kind, expected):
    assert _cloud.databricks_type(pa.field("payload", kind)) == expected


def test_postgres_copy_scalar_array_text_is_distinct_from_json_and_escapes_data():
    sink, uri, server = sink_fixture("postgres")
    values = ['a"b', "a\\b", "NULL", None, "", "x'); DROP TABLE t; --"]
    batch = pa.record_batch([pa.array([values], pa.list_(pa.string()))], names=["tags"])
    assert sink.write(uri, "t", [batch]) == 1
    assert server.rows("t")[0][0] == '{"a\\"b","a\\\\b","NULL",NULL,"","x\'); DROP TABLE t; --"}'
    assert all("DROP TABLE t" not in statement for statement in server.statements())


@pytest.mark.parametrize("dialect", ["postgres", "mysql"])
def test_native_json_columns_preserve_decimal_zone_and_map_data(dialect):
    sink, uri, server = sink_fixture(dialect)
    timestamp = dt.datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=dt.UTC)
    kind = pa.struct([("amount", pa.decimal128(12, 4)), ("at", pa.timestamp("us", "UTC"))])
    batch = pa.record_batch(
        [pa.array([{"amount": Decimal("1.2300"), "at": timestamp}], kind)], names=["payload"]
    )
    assert sink.write(uri, "t", [batch]) == 1
    decoded = json.loads(server.rows("t")[0][0])
    assert decoded == {"amount": "1.2300", "at": "2026-01-02T03:04:05.123456+00:00"}


def test_databricks_values_are_bound_json_with_a_safely_quoted_from_json_schema():
    field = pa.field("payload", pa.struct([("a'b", pa.list_(pa.int32()))]))
    mark, convert = binding(field)
    assert mark == "from_json(?, 'STRUCT<`a''b`: ARRAY<INT>>', map('mode', 'FAILFAST'))"
    assert json.loads(convert({"a'b": [1, None]})) == {"a'b": [1, None]}
    assert convert(None) is None
    sink, uri, server = sink_fixture("databricks")
    batch = pa.record_batch([pa.array([{"a'b": [1, None]}], field.type)], schema=pa.schema([field]))
    assert sink.write(uri, "t", [batch]) == 1
    statement, values = server.inserts()[0]
    assert mark in statement
    assert values == ['{"a\'b":[1,null]}']


def test_databricks_nested_timestamp_parameters_use_iso_t_and_decimal_text():
    kind = pa.struct([("at", pa.timestamp("us", "UTC")), ("amount", pa.decimal128(12, 4))])
    mark, convert = binding(pa.field("payload", kind))
    assert "TIMESTAMP" in mark and "DECIMAL(12,4)" in mark and "FAILFAST" in mark
    value = convert(
        {"at": dt.datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=dt.UTC), "amount": Decimal("1.2300")}
    )
    assert json.loads(value) == {"at": "2026-01-02T03:04:05.123456+00:00", "amount": "1.2300"}


def test_databricks_non_string_map_key_shape_is_refused_before_connection():
    sink, uri, server = sink_fixture("databricks")
    with pytest.raises(ShapeError, match="payload.*STRING map keys.*nested=json"):
        sink.write(uri, "t", [], schema=pa.schema([("payload", pa.map_(pa.int32(), pa.string()))]))
    assert not server.events


def test_databricks_duplicate_string_map_keys_are_not_collapsed_before_connection():
    sink, uri, server = sink_fixture("databricks")
    batch = pa.record_batch(
        [pa.array([[("x", 1), ("x", 2)]], pa.map_(pa.string(), pa.int32()))], names=["payload"]
    )
    with pytest.raises(ShapeError, match="payload.*duplicate map keys.*nested=json"):
        sink.write(uri, "t", [batch])
    assert not server.events


def test_databricks_later_duplicate_map_key_batch_keeps_prior_committed_rows():
    sink, uri, server = sink_fixture("databricks")
    dtype = pa.map_(pa.string(), pa.int32())
    first = pa.record_batch([pa.array([[("x", 1)]], dtype)], names=["payload"])
    later = pa.record_batch([pa.array([[("x", 2), ("x", 3)]], dtype)], names=["payload"])
    with pytest.raises(WriteError, match="payload.*duplicate map keys") as error:
        sink.write(uri, "t", [first, later], commit_rows=1)
    assert error.value.rows_committed == 1
    assert server.rows("t") == [('{"x":1}',)]


@pytest.mark.parametrize("dialect", ["postgres", "mysql", "databricks"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_native_json_nonfinite_values_are_named_failures_without_silent_nulls(dialect, value):
    sink, uri, server = sink_fixture(dialect)
    batch = pa.record_batch(
        [pa.array([{"value": value}], pa.struct([("value", pa.float64())]))], names=["payload"]
    )
    with pytest.raises(ShapeError, match="payload.*JSON"):
        sink.write(uri, "t", [batch])
    if dialect == "databricks":
        assert not server.events


@pytest.mark.parametrize(
    "kind,expected",
    [
        (pa.list_(pa.int32()), "ARRAY"),
        (pa.struct([("x", pa.int32())]), "OBJECT"),
        (pa.map_(pa.string(), pa.int32()), "OBJECT"),
    ],
)
def test_snowflake_typed_option_keeps_variant_default_and_selects_array_object(kind, expected):
    field = pa.field("payload", kind)
    assert _cloud.snowflake_type(field) == "VARIANT"
    assert _cloud.snowflake_type(field, nested="typed") == expected


@pytest.mark.parametrize("mode", ["native", "typed"])
def test_snowflake_nested_copy_preserves_null_array_positions_with_vectorized_scanner(mode):
    sink, uri, server = sink_fixture("snowflake")
    batch = pa.record_batch([pa.array([[1, None, 2]], pa.list_(pa.int32()))], names=["payload"])
    assert sink.write(uri, "t", [batch], nested=mode) == 1
    statement = next(sql for sql in server.statements() if sql.startswith("COPY INTO"))
    assert "USE_VECTORIZED_SCANNER = TRUE" in statement
    assert "USE_LOGICAL_TYPE = TRUE" in statement
    assert server.rows("t") == [([1, None, 2],)]


@pytest.mark.parametrize(
    "kind",
    [
        pa.struct([]),
        pa.map_(pa.int16(), pa.string()),
        pa.list_(pa.union([pa.field("a", pa.int32()), pa.field("b", pa.string())], mode="dense")),
        pa.list_(pa.decimal256(50, 2)),
        pa.list_(pa.decimal128(3, 5)),
    ],
)
@pytest.mark.parametrize("mode", ["native", "json", "typed"])
def test_snowflake_unrepresentable_parquet_shapes_refuse_before_connection(kind, mode):
    sink, uri, server = sink_fixture("snowflake")
    with pytest.raises(ShapeError, match="payload.*JSON string") as error:
        sink.write(uri, "t", [], schema=pa.schema([("payload", kind)]), nested=mode)
    assert "use nested=json" not in str(error.value)
    assert not server.events


def test_snowflake_duplicate_object_map_keys_refuse_before_connection():
    sink, uri, server = sink_fixture("snowflake")
    batch = pa.record_batch(
        [pa.array([[("x", 1), ("x", 2)]], pa.map_(pa.string(), pa.int32()))], names=["payload"]
    )
    with pytest.raises(ShapeError, match="payload.*duplicate map keys"):
        sink.write(uri, "t", [batch])
    assert not server.events


def test_databricks_nested_decimal_scale_cannot_exceed_precision_before_connection():
    sink, uri, server = sink_fixture("databricks")
    with pytest.raises(ShapeError, match="payload.*decimal.*nested=json"):
        sink.write(uri, "t", [], schema=pa.schema([("payload", pa.list_(pa.decimal128(3, 5)))]))
    assert not server.events


@pytest.mark.parametrize("dialect", ["postgres", "mysql", "snowflake", "databricks"])
def test_invalid_nested_option_is_refused_before_connection(dialect):
    sink, uri, server = sink_fixture(dialect)
    with pytest.raises(ShapeError, match="nested"):
        sink.write(
            uri, "t", [], schema=pa.schema([("payload", pa.list_(pa.int32()))]), nested="bad"
        )
    assert not server.events


@pytest.mark.parametrize("dialect", ["postgres", "databricks"])
def test_unsupported_nested_shape_is_refused_before_connection_with_alternative(dialect):
    sink, uri, server = sink_fixture(dialect)
    kind = pa.list_(pa.list_(pa.int32())) if dialect == "postgres" else pa.list_(pa.time64("us"))
    with pytest.raises(ShapeError, match="payload.*nested=json"):
        sink.write(uri, "t", [], schema=pa.schema([("payload", kind)]))
    assert not server.events


@pytest.mark.parametrize("dialect", ["postgres", "mysql", "snowflake", "databricks"])
def test_explicit_legacy_json_option_and_uri_do_not_reach_driver_options(dialect):
    sink, uri, server = sink_fixture(dialect)
    suffix = "&nested=json" if "?" in uri else "?nested=json"
    batch = pa.record_batch([pa.array([[1, 2]], pa.list_(pa.int32()))], names=["payload"])
    assert sink.write(uri + suffix, "t", [batch]) == 1
    connections = [event for event in server.events if event[0] == "connect"]
    assert connections
    assert "nested" not in connections[0][1]
