"""W9-03 cloud DDL and driver values, entirely offline."""

import datetime as dt

import pyarrow as pa
import pytest
from shape_databases import _cloud, _sql


@pytest.mark.parametrize(
    "typ,snow,db",
    [
        (pa.int8(), "NUMBER(3,0)", "TINYINT"),
        (pa.int16(), "NUMBER(5,0)", "SMALLINT"),
        (pa.int32(), "NUMBER(10,0)", "INT"),
        (pa.int64(), "NUMBER(19,0)", "BIGINT"),
        (pa.uint8(), "NUMBER(5,0)", "SMALLINT"),
        (pa.uint16(), "NUMBER(10,0)", "INT"),
        (pa.uint32(), "NUMBER(19,0)", "BIGINT"),
        (pa.uint64(), "DECIMAL(20,0)", "DECIMAL(20,0)"),
        (pa.float32(), "FLOAT(24)", "FLOAT"),
        (pa.float64(), "FLOAT", "DOUBLE"),
        (pa.timestamp("ms"), "TIMESTAMP_NTZ(3)", "TIMESTAMP_NTZ"),
        (pa.timestamp("us", "UTC"), "TIMESTAMP_TZ(6)", "TIMESTAMP"),
        (pa.time64("us"), "TIME(6)", "TIME(6)"),
    ],
)
def test_wanted1_cloud_arrow_maps(typ, snow, db):
    field = pa.field("value", typ)
    assert _cloud.snowflake_type(field) == snow
    assert _cloud.databricks_type(field) == db


@pytest.mark.parametrize("dialect", ["postgres", "mysql"])
def test_wanted2_later_batch_never_sizes_ddl(dialect):
    schema = pa.schema([("text", pa.string())])
    first = pa.RecordBatch.from_pydict({"text": ["a"]})
    larger = pa.RecordBatch.from_pydict({"text": ["b" * 10000]})
    assert _sql.create_table_sql(
        None, "items", schema, dialect, first=first
    ) == _sql.create_table_sql(None, "items", schema, dialect, first=larger)
    assert "TEXT" in _sql.create_table_sql(None, "items", schema, dialect, first=first)


@pytest.mark.parametrize("dialect", ["postgres", "mysql"])
def test_wanted3_driver_utc_values_and_exact_ddl(dialect):
    value = dt.datetime(2020, 1, 1, 1, 2, 3, 999999, tzinfo=dt.timezone(dt.timedelta(hours=9)))
    schema = pa.schema(
        [
            ("small", pa.int16()),
            ("amount", pa.decimal128(12, 4)),
            ("at", pa.timestamp("us", "Asia/Tokyo")),
        ]
    )
    sql = _sql.create_table_sql(None, "items", schema, dialect)
    assert "SMALLINT" in sql
    assert ("NUMERIC(12,4)" if dialect == "postgres" else "DECIMAL(12,4)") in sql
    actual = _sql.converters_for(schema, dialect)[2](value)
    assert (
        actual == value.astimezone(dt.UTC).replace(tzinfo=None)
        if dialect == "mysql"
        else actual == value.astimezone(dt.UTC)
    )
    assert _sql.converters_for(schema, dialect)[2](None) is None


@pytest.mark.parametrize(
    "name,uri",
    [
        ("postgres", "postgresql://shape@db.example/shape"),
        ("mysql", "mysql://shape@db.example/shape"),
        ("snowflake", "snowflake://shape@account/shape"),
        ("databricks", "databricks://host/sql/1.0/warehouses/id?catalog=main&schema=demo"),
    ],
)
def test_wanted5_offline_ddl_never_resolves_credentials(name, uri, monkeypatch):
    from shape_databases import DatabricksSink, MySqlSink, PostgresSink, SnowflakeSink

    cls = {
        "postgres": PostgresSink,
        "mysql": MySqlSink,
        "snowflake": SnowflakeSink,
        "databricks": DatabricksSink,
    }[name]
    sink = cls(connect=lambda **kwargs: pytest.fail("connected during DDL"))
    monkeypatch.setattr(
        sink, "resolve_auth", lambda *args: pytest.fail("resolved credentials during DDL")
    )
    assert (
        "SMALLINT" in sink.ddl(uri, "items", pa.schema([("small", pa.int16())]))
        if name != "snowflake"
        else "NUMBER(5,0)" in sink.ddl(uri, "items", pa.schema([("small", pa.int16())]))
    )


@pytest.mark.parametrize("dialect", ["postgres", "mysql"])
def test_wanted3_fake_roundtrip_extremes_catalog_ddl_and_dataset_id(dialect):
    from decimal import Decimal

    from shape_databases import MySqlSink, PostgresSink
    from shape_databases.testing import FakeServer

    from shape.repro import dataset_id

    schema = pa.schema(
        [
            ("small", pa.int16()),
            ("unsigned", pa.uint64()),
            ("amount", pa.decimal128(12, 4)),
            ("at", pa.timestamp("us", "Asia/Tokyo")),
            ("text", pa.string()),
        ]
    )
    batch = pa.RecordBatch.from_pydict(
        {
            "small": [-32768, 32767, None],
            "unsigned": [0, 2**64 - 1, None],
            "amount": [Decimal("-99999999.9999"), Decimal("99999999.9999"), None],
            "at": [
                dt.datetime(2020, 1, 1, 0, 0, 0, 1, tzinfo=dt.UTC),
                dt.datetime(2020, 1, 1, 0, 0, 0, 999999, tzinfo=dt.UTC),
                None,
            ],
            "text": ["a", "b" * 10000, None],
        },
        schema=schema,
    )
    server = FakeServer(dialect)
    cls = PostgresSink if dialect == "postgres" else MySqlSink
    sink = cls(connect=server.connect)
    uri = f"{'postgresql' if dialect == 'postgres' else 'mysql'}://shape@localhost/shape"
    assert sink.write(uri, "items", [batch.slice(0, 1), batch.slice(1)]) == 3
    ddl = next(sql for sql in server.statements() if sql.startswith("CREATE TABLE"))
    assert "SMALLINT" in ddl and "(20,0)" in ddl and "(12,4)" in ddl and "TEXT" in ddl
    rows = server.rows("items")
    # A MySQL driver returns UTC-naive TIMESTAMP; an explicit Arrow zoned read schema
    # restores the UTC-aware value. PostgreSQL returns aware values directly.
    read = pa.Table.from_pylist(
        [dict(zip(schema.names, row, strict=True)) for row in rows],
        schema=pa.schema(
            [
                schema.field(i) if i != 3 else pa.field("at", pa.timestamp("us", "UTC"))
                for i in range(len(schema))
            ]
        ),
    )
    written = pa.Table.from_batches([batch]).set_column(
        3, "at", batch.column(3).cast(pa.timestamp("us", "UTC"))
    )
    assert dataset_id({"items": read}) == dataset_id({"items": written})
    assert read["at"][0].as_py().utcoffset() == dt.timedelta(0)
    if dialect == "mysql":
        assert server.events[0][1]["init_command"] == "SET time_zone = '+00:00'"


@pytest.mark.parametrize("name", ["postgres", "mysql", "snowflake", "databricks"])
def test_wanted1_bad_decimal_names_column_before_connection(name):
    from shape_databases import DatabricksSink, MySqlSink, PostgresSink, SnowflakeSink

    cls = {
        "postgres": PostgresSink,
        "mysql": MySqlSink,
        "snowflake": SnowflakeSink,
        "databricks": DatabricksSink,
    }[name]
    sink = cls(connect=lambda **kwargs: pytest.fail("connected with invalid decimal"))
    uri = {
        "postgres": "postgresql://shape@localhost/shape",
        "mysql": "mysql://shape@localhost/shape",
        "snowflake": "snowflake://shape@account/db",
        "databricks": "databricks://host/sql/1.0/warehouses/id?catalog=main&schema=demo",
    }[name]
    with pytest.raises(ValueError, match="amount"):
        sink.ddl(
            uri,
            "items",
            pa.schema([("amount", pa.float64())]),
            columns={"amount": {"type": "decimal"}},
        )


@pytest.mark.parametrize("name", ["postgres", "mysql", "snowflake", "databricks"])
def test_wanted1_actual_write_refuses_incomplete_decimal_before_connection(name):
    from shape_databases import DatabricksSink, MySqlSink, PostgresSink, SnowflakeSink

    cls = {
        "postgres": PostgresSink,
        "mysql": MySqlSink,
        "snowflake": SnowflakeSink,
        "databricks": DatabricksSink,
    }[name]
    sink = cls(connect=lambda **kwargs: pytest.fail("connected with invalid decimal"))
    uri = {
        "postgres": "postgresql://shape@localhost/shape",
        "mysql": "mysql://shape@localhost/shape",
        "snowflake": "snowflake://shape@account/db",
        "databricks": "databricks://host/sql/1.0/warehouses/id?catalog=main&schema=demo",
    }[name]
    with pytest.raises(ValueError, match="amount"):
        sink.write(
            uri,
            "items",
            [pa.RecordBatch.from_pydict({"amount": [1.5]})],
            columns={"amount": {"type": "decimal"}},
        )


def test_wanted1_dictionary_normalization_retains_uuid_metadata():
    schema = pa.schema(
        [
            pa.field("id", pa.string(), metadata={b"type": b"uuid"}),
            pa.field("text", pa.dictionary(pa.int8(), pa.string())),
        ]
    )
    batch = pa.RecordBatch.from_arrays(
        [
            pa.array(["00000000-0000-0000-0000-000000000001"]),
            pa.array(["a"]).dictionary_encode().cast(pa.dictionary(pa.int8(), pa.string())),
        ],
        schema=schema,
    )
    normalized = _sql.normalize(batch)
    assert normalized.schema.field("id").metadata == {b"type": b"uuid"}
    assert "UUID" in _sql.create_table_sql(None, "items", normalized.schema, "postgres")


def test_wanted3_cloud_sessions_are_utc():
    from shape_databases import DatabricksSink, SnowflakeSink

    snow = SnowflakeSink()
    plan = snow.plan("snowflake://shape@account/db", "items", {})
    assert snow.connect_params(plan)["session_parameters"] == {"TIMEZONE": "UTC"}
    db = DatabricksSink()
    plan = db.plan("databricks://host/sql/1.0/warehouses/id?catalog=main&schema=demo", "items", {})
    assert db.connect_params(plan)["session_configuration"] == {"spark.sql.session.timeZone": "UTC"}


def test_wanted3_snowflake_copy_respects_parquet_logical_types():
    from shape_databases import SnowflakeSink

    assert "USE_LOGICAL_TYPE = TRUE" in SnowflakeSink.copy_sql('"items"', '@%"items"')
