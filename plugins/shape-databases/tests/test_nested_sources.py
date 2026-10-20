"""Actual matching Source routes read stored sink data and physical catalog types."""

import datetime as dt
from decimal import Decimal

import pyarrow as pa
import pytest
from shape_databases import MySqlSink, PostgresSink
from shape_databases.nested_readback import restore_json_column
from shape_databases.sources import MySqlSource, PostgresSource
from shape_databases.testing import FakeServer

pytestmark = pytest.mark.conformance


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


def test_databricks_native_catalog_and_matching_source_read_stored_bound_values():
    from shape_databases import DatabricksSink
    from shape_databases.cloud_readers import DatabricksSource
    from shape_databases.nested_readback import databricks_type
    from shape_databases.testing import FakeDatabricks

    server = FakeDatabricks(default_schema="demo")
    uri = "databricks://host/warehouse?schema=demo"
    kind = pa.list_(
        pa.struct(
            [
                ("amount", pa.decimal128(12, 4)),
                ("at", pa.timestamp("us", "UTC")),
                ("entries", pa.map_(pa.string(), pa.list_(pa.int32()))),
                ("bytes", pa.binary()),
            ]
        )
    )
    expected = pa.table(
        {
            "payload": pa.array(
                [
                    [
                        {
                            "amount": Decimal("12.3400"),
                            "at": dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
                            "entries": [("x", [1, None])],
                            "bytes": b"\x00\xff",
                        },
                        None,
                    ],
                    [],
                    None,
                ],
                kind,
            )
        }
    )
    assert DatabricksSink(connect=server.connect).write(uri, "data", expected.to_batches()) == 3
    ddl = next(sql for sql in server.statements() if sql.startswith("CREATE TABLE"))
    physical = ddl.splitlines()[1].strip().removeprefix("`payload` ").removesuffix(" NULL")
    assert physical.startswith("ARRAY<STRUCT<") and "MAP<STRING, ARRAY<INT>>" in physical
    declared = pa.field("payload", databricks_type(physical, "payload"))
    # Emulate SQL from_json on captured bound JSON and the actual physical type. Source
    # consumers receive native Arrow data, without schema reconstruction.
    stored = restore_json_column([row[0] for row in server.rows("data", "demo")], declared)
    source = DatabricksSource(connect=server.connect)
    columns = (
        "SELECT column_name, full_data_type, is_nullable, numeric_precision, numeric_scale "
        "FROM information_schema.columns WHERE table_schema=? AND table_name=? "
        "ORDER BY ordinal_position"
    )
    server.catalog_answers[columns] = (
        ["name", "type", "nullable", "precision", "scale"],
        [("payload", physical, "YES", None, None)],
    )
    query = "SELECT `payload` FROM `demo`.`data`"
    server.arrow_results[query] = pa.table({"payload": stored})
    actual = pa.Table.from_batches(source.read(uri + "&table=data", batch_size=1))
    assert actual.column("payload").type == expected.column("payload").type
    assert actual.to_pylist() == expected.to_pylist()
    assert all("to_json(" not in sql.lower() for sql in server.statements())


@pytest.mark.parametrize("dialect", ["snowflake", "databricks"])
def test_cloud_sources_reject_writer_nested_uri_before_connection(dialect):
    from shape_databases.cloud_readers import DatabricksSource, SnowflakeSource
    from shape_databases.testing import FakeDatabricks, FakeSnowflake

    from shape.errors import ShapeError

    server = FakeSnowflake() if dialect == "snowflake" else FakeDatabricks()
    source = (
        SnowflakeSource(connect=server.connect)
        if dialect == "snowflake"
        else DatabricksSource(connect=server.connect)
    )
    uri = (
        "snowflake://user@account/db/PUBLIC?table=data&nested=native"
        if dialect == "snowflake"
        else "databricks://host/warehouse?table=data&nested=native"
    )
    with pytest.raises(ShapeError, match="source.*nested"):
        list(source.read(uri))
    assert not server.events


@pytest.mark.parametrize("mode", ["native", "typed"])
@pytest.mark.parametrize("container", ["list", "struct", "map"])
def test_snowflake_matching_source_restores_json_from_stored_parquet_objects(mode, container):
    import json

    from shape_databases import SnowflakeSink
    from shape_databases.cloud_readers import SnowflakeSource
    from shape_databases.testing import FakeSnowflake

    examples = {
        "list": (
            pa.list_(
                pa.struct([("amount", pa.decimal128(12, 4)), ("at", pa.timestamp("us", "UTC"))])
            ),
            [
                [
                    {"amount": Decimal("12.3400"), "at": dt.datetime(2026, 1, 2, tzinfo=dt.UTC)},
                    None,
                ],
                [],
                None,
            ],
        ),
        "struct": (
            pa.struct([("parts", pa.list_(pa.int32())), ("text", pa.string())]),
            [{"parts": [1, None], "text": "[]"}, {"parts": [], "text": None}, None],
        ),
        "map": (
            pa.map_(pa.string(), pa.list_(pa.int32())),
            [[("x", [1, None]), ("empty", [])], [], None],
        ),
    }
    kind, values = examples[container]
    field = pa.field("payload", kind)
    expected = pa.table({"payload": pa.array(values, kind)})
    server = FakeSnowflake()
    uri = "snowflake://user@account/db/PUBLIC"
    assert (
        SnowflakeSink(connect=server.connect).write(uri, "data", expected.to_batches(), nested=mode)
        == 3
    )
    ddl = next(sql for sql in server.statements() if sql.startswith("CREATE TABLE"))
    physical = ddl.splitlines()[1].strip().removeprefix('"payload" ').removesuffix(" NULL")
    assert physical == (
        "VARIANT" if mode == "native" else "ARRAY" if container == "list" else "OBJECT"
    )
    staged = server.uploaded[0][1]
    assert staged.column("payload").type == kind
    assert isinstance(server.rows("data", "PUBLIC")[0][0], (dict, list))

    def to_json_value(value, dtype):
        if value is None:
            return None
        if pa.types.is_struct(dtype):
            return {child.name: to_json_value(value[child.name], child.type) for child in dtype}
        if pa.types.is_list(dtype):
            return [to_json_value(item, dtype.value_type) for item in value]
        if pa.types.is_map(dtype):
            return {key: to_json_value(item, dtype.item_type) for key, item in value}
        if isinstance(value, (dt.datetime, dt.date, dt.time)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        return value

    # Simulate TO_JSON from captured physical Parquet values, not expected Arrow rows.
    raw = [
        None if row[0] is None else json.dumps(to_json_value(row[0], staged.column("payload").type))
        for row in server.rows("data", "PUBLIC")
    ]
    source = SnowflakeSource(connect=server.connect)
    columns = (
        "SELECT column_name, data_type, is_nullable, numeric_precision, numeric_scale "
        "FROM information_schema.columns WHERE table_schema=%s AND table_name=%s "
        "ORDER BY ordinal_position"
    )
    server.catalog_answers[columns] = (
        ["name", "type", "nullable", "precision", "scale"],
        [("payload", physical, "YES", None, None)],
    )
    query = 'SELECT TO_JSON("payload") AS "payload" FROM "PUBLIC"."data"'
    server.arrow_results[query] = pa.table({"payload": pa.array(raw, pa.string())})
    actual = pa.Table.from_batches(source.read(uri + "?table=data"))
    assert actual.column("payload").type == pa.string()
    assert restore_json_column(actual.column("payload").to_pylist(), field).equals(
        expected.column("payload").combine_chunks()
    )


def test_postgres_jsonb_nanosecond_timestamp_survives_actual_source_json_restoration():
    server = FakeServer("postgres")
    field = pa.field("payload", pa.struct([("at", pa.timestamp("ns", "UTC"))]))
    values = pa.StructArray.from_arrays(
        [pa.array([1767323045123456789], pa.int64()).cast(pa.timestamp("ns", "UTC"))],
        fields=list(field.type),
    )
    batch = pa.record_batch([values], schema=pa.schema([field]))
    uri = "postgres://shape@localhost/public"
    assert PostgresSink(connect=server.connect).write(uri, "data", [batch]) == 1
    raw = pa.Table.from_batches(PostgresSource(connect=server.connect).read(uri + "?table=data"))
    actual = restore_json_column(raw.column("payload").to_pylist(), field)
    assert actual.field("at").cast(pa.int64()).to_pylist() == [1767323045123456789]
