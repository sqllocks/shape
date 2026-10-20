"""Cloud source catalog/type acceptance, without a cloud account."""

from decimal import Decimal

import pyarrow as pa
import pytest


def cloud_arrow_type(*args):
    # Import when the case executes so the main-baseline run records every refusal,
    # transport and catalog case rather than ending during collection.
    from shape_databases.cloud_sources import cloud_arrow_type as resolve

    return resolve(*args)


@pytest.mark.parametrize(
    ("precision", "expected"),
    [
        (1, pa.int8()),
        (2, pa.int8()),
        (3, pa.int16()),
        (4, pa.int16()),
        (5, pa.int32()),
        (9, pa.int32()),
        (10, pa.int64()),
        (18, pa.int64()),
        (19, pa.decimal128(19, 0)),
        (38, pa.decimal128(38, 0)),
    ],
)
def test_snowflake_integer_digits(precision, expected):
    assert cloud_arrow_type("snowflake", "NUMBER", precision, 0) == expected
    # The complete signed p-digit range fits, including the negative endpoint.
    pa.array([-(10**precision - 1), 10**precision - 1], type=expected)


@pytest.mark.parametrize(
    ("dialect", "kind", "expected"),
    [
        ("snowflake", "FLOAT", pa.float64()),
        ("snowflake", "TIMESTAMP_NTZ", pa.timestamp("us")),
        ("snowflake", "TIMESTAMP_TZ", pa.timestamp("us", "UTC")),
        ("snowflake", "TIMESTAMP_LTZ", pa.timestamp("us", "UTC")),
        ("databricks", "TINYINT", pa.int8()),
        ("databricks", "SMALLINT", pa.int16()),
        ("databricks", "INT", pa.int32()),
        ("databricks", "BIGINT", pa.int64()),
        ("databricks", "TIMESTAMP", pa.timestamp("us", "UTC")),
        ("databricks", "TIMESTAMP_NTZ", pa.timestamp("us")),
    ],
)
def test_cloud_type_widths_and_zones(dialect, kind, expected):
    assert cloud_arrow_type(dialect, kind) == expected


@pytest.mark.parametrize("dialect", ["snowflake", "databricks"])
def test_decimal_scale_and_precision(dialect):
    dtype = cloud_arrow_type(dialect, "DECIMAL", 38, 9)
    assert dtype == pa.decimal128(38, 9)
    assert pa.array([Decimal("-123456789.123456789")], type=dtype)[0].as_py() == Decimal(
        "-123456789.123456789"
    )
    for precision, scale in [(0, 0), (39, 0), (5, 6), (5, -1)]:
        with pytest.raises(ValueError):
            cloud_arrow_type(dialect, "DECIMAL", precision, scale)


@pytest.mark.parametrize(
    "dialect,types",
    [
        ("snowflake", ["VARIANT", "ARRAY", "OBJECT"]),
        ("databricks", ["ARRAY<INT>", "STRUCT<a:INT>", "MAP<STRING,INT>"]),
    ],
)
def test_nested_catalog_types_are_json_text(dialect, types):
    for kind in types:
        assert cloud_arrow_type(dialect, kind) == pa.string()


def cloud_fixture(dialect):
    from shape_databases.cloud_readers import DatabricksSource, SnowflakeSource
    from shape_databases.testing import FakeDatabricks, FakeSnowflake

    snow = dialect == "snowflake"
    server = FakeSnowflake() if snow else FakeDatabricks()
    source = (
        SnowflakeSource(connect=server.connect)
        if snow
        else DatabricksSource(connect=server.connect)
    )
    uri = (
        "snowflake://user@account/db/PUBLIC?warehouse=WH"
        if snow
        else "databricks://host/sql/1.0/warehouses/id?catalog=main&schema=demo"
    )
    schema = "PUBLIC" if snow else "demo"
    marker = "%s" if snow else "?"
    columns = (
        "SELECT column_name, data_type, is_nullable, numeric_precision, numeric_scale FROM "
        "information_schema.columns WHERE table_schema="
        + marker
        + " AND table_name="
        + marker
        + " ORDER BY ordinal_position"
    )
    server.catalog_answers[columns] = (
        ["column_name", "data_type", "is_nullable", "numeric_precision", "numeric_scale"],
        [("id", "NUMBER" if snow else "INT", "NO", 9, 0)],
    )
    if snow:
        server.catalog_answers[
            "SELECT table_name, COALESCE(row_count,0) FROM information_schema.tables WHERE "
            "table_schema=%s AND table_type='BASE TABLE' ORDER BY table_name"
        ] = (["table_name", "row_count"], [("items", 10000)])
        server.catalog_answers['SHOW PRIMARY KEYS IN TABLE "PUBLIC"."items"'] = (
            ["column_name", "key_sequence"],
            [("id", 1)],
        )
        server.catalog_answers['SHOW IMPORTED KEYS IN TABLE "PUBLIC"."items"'] = (
            ["fk_name", "pk_table_name", "pk_column_name", "fk_column_name", "key_sequence"],
            [],
        )
        server.catalog_answers["SELECT CURRENT_WAREHOUSE()"] = (["warehouse"], [("WH",)])
    else:
        server.catalog_answers[
            "SELECT table_name FROM information_schema.tables WHERE table_schema=? AND "
            "table_type='BASE TABLE' ORDER BY table_name"
        ] = (["table_name"], [("items",)])
        server.catalog_answers["DESCRIBE TABLE EXTENDED `demo`.`items`"] = (
            ["col_name", "data_type"],
            [("Statistics", "40000 bytes, 10000 rows")],
        )
        # Constraint queries get explicit empty catalog answers below.
    fields = pa.schema(
        [
            pa.field(
                "id",
                pa.int32(),
                nullable=False,
                metadata={b"shape.sql_type": b"NUMBER" if snow else b"INT"},
            )
        ]
    )
    query = source._select(schema, "items", fields)
    table = pa.table({"id": pa.array([1, 2], type=pa.int32())})
    server.arrow_results[query] = table
    server.arrow_results[
        query + (" TABLESAMPLE (0.04) LIMIT 2" if snow else " TABLESAMPLE (0.04 PERCENT) LIMIT 2")
    ] = table
    return source, server, uri, schema, fields


@pytest.mark.parametrize("dialect", ["snowflake", "databricks"])
def test_read_uses_connector_arrow_and_declared_schema(dialect):
    source, server, uri, _, _ = cloud_fixture(dialect)
    batches = list(source.read(uri + "&table=items", batch_size=1))
    assert [b.num_rows for b in batches] == [1, 1]
    assert batches[0].schema.field("id").type == pa.int32()
    assert batches[0].schema.field("id").nullable is False
    fetch = "fetch_arrow_batches" if dialect == "snowflake" else "fetchall_arrow"
    assert any(event[0] == fetch for event in server.events)
    assert server.events[-1][0] == "close"


@pytest.mark.parametrize("dialect", ["snowflake", "databricks"])
def test_source_default_batch_size_does_not_inherit_sink_write_batches(dialect):
    source, server, uri, _, _ = cloud_fixture(dialect)
    assert source._plan(uri + "&table=items", {}, True).batch_size == 65536
    assert source._plan(uri + "&table=items", {"batch_size": 17}, True).batch_size == 17
    assert not server.events


@pytest.mark.parametrize("dialect", ["snowflake", "databricks"])
@pytest.mark.parametrize(
    "suffix", ["&password=never-in-uri", "&token=never-in-uri", "&table=", "&table=x&table=y"]
)
def test_source_refuses_before_connection(dialect, suffix):
    source, server, uri, _, _ = cloud_fixture(dialect)
    with pytest.raises(Exception) as exc:
        list(source.read(uri + suffix))
    assert "never-in-uri" not in str(exc.value)
    assert not server.events


@pytest.mark.parametrize("dialect", ["snowflake", "databricks"])
def test_catalog_counts_and_bounded_tablesample(dialect):
    source, server, uri, schema, fields = cloud_fixture(dialect)
    plan = source._plan(uri, {}, False)
    with source._connection(plan) as conn:
        cur = conn.cursor()
        assert source._tables(cur, schema) == [("items", 10000)]
        sample, method = source._sample(cur, plan, schema, "items", fields, 2, 10000, [])
        assert sample.num_rows == 2
        assert method == "tablesample"
        sample, method = source._sample(cur, plan, schema, "items", fields, 0, 10000, [])
        assert sample.num_rows == 0
        assert method == "catalog_only"


def test_snowflake_declared_keys_and_actual_warehouse():
    source, server, uri, schema, _ = cloud_fixture("snowflake")
    plan = source._plan(uri, {}, False)
    with source._connection(plan) as conn:
        cur = conn.cursor()
        assert source._keys(cur, schema, "items") == (["id"], [])
        assert source._provenance(cur, plan) == {"warehouse": "WH"}


def test_databricks_missing_catalog_statistics_is_explicit():
    source, server, uri, schema, _ = cloud_fixture("databricks")
    server.catalog_answers["DESCRIBE TABLE EXTENDED `demo`.`items`"] = (
        ["col_name", "data_type"],
        [],
    )
    with source._connection(source._plan(uri, {}, False)) as conn:
        with pytest.raises(Exception, match="statistics are missing"):
            source._tables(conn.cursor(), schema)


def test_snowflake_profile_persistence_and_sampling(tmp_path):
    """SHAPE-CLOUD-001: catalog/sample fields survive the existing profile format."""
    from shape.profile.reference import load, save

    source, _, uri, _, _ = cloud_fixture("snowflake")
    profile = source.profile_database(uri, sample_rows=2)
    item = profile.tables["items"]
    assert item["row_count"] == 10000
    assert item["sampled_rows"] == 2
    assert item["primary_key"] == ["id"]
    assert profile.provenance == {"warehouse": "WH"}
    path = tmp_path / "cloud.shape"
    save(profile, path)
    restored = load(path)
    assert restored.tables["items"]["sampled_rows"] == 2
    assert restored.tables["items"]["row_count"] == 10000
    assert restored.provenance == {"warehouse": "WH"}


@pytest.mark.parametrize("dialect", ["snowflake", "databricks"])
def test_authentication_failure_never_exposes_option_secret(dialect):
    source, server, uri, _, _ = cloud_fixture(dialect)
    secret = "credential-do-not-echo"
    server.fail_connect = "connection refused " + secret
    option = "password" if dialect == "snowflake" else "token"
    with pytest.raises(Exception) as exc:
        list(source.read(uri + "&table=items", **{option: secret}))
    assert secret not in str(exc.value)
    assert exc.value.__context__ is None


def test_snowflake_informational_foreign_key_evidence():
    source, server, uri, _, _ = cloud_fixture("snowflake")
    server.catalog_answers['SHOW IMPORTED KEYS IN TABLE "PUBLIC"."items"'] = (
        ["fk_name", "pk_table_name", "pk_column_name", "fk_column_name", "key_sequence"],
        [("items_parent", "parent", "id", "id", 1)],
    )
    profile = source.profile_database(uri, sample_rows=2)
    assert profile.to_dict()["relationships"][0]["evidence"] == "declared"
    assert profile.to_dict()["relationships"][0]["parent_columns"] == ["id"]
    assert profile.tables["items"]["columns"]["id"]["fk_evidence"] == "declared"


@pytest.mark.parametrize("dialect", ["snowflake", "databricks"])
def test_nested_values_are_projected_to_json_before_arrow_fetch(dialect):
    source, server, uri, schema, _ = cloud_fixture(dialect)
    column_query = next(q for q in server.catalog_answers if q.startswith("SELECT column_name"))
    names, _ = server.catalog_answers[column_query]
    server.catalog_answers[column_query] = (
        names,
        [("payload", "VARIANT" if dialect == "snowflake" else "STRUCT", "YES", None, None)],
    )
    fields = pa.schema([pa.field("payload", pa.string(), metadata={b"shape.encoding": b"json"})])
    query = source._select(schema, "items", fields)
    assert "TO_JSON" in query.upper()
    server.arrow_results[query] = pa.table({"payload": ['{"key":[1,null]}', None]})
    actual = pa.Table.from_batches(list(source.read(uri + "&table=items")))
    assert actual.column("payload").to_pylist() == ['{"key":[1,null]}', None]
    assert actual.schema.field("payload").metadata[b"shape.encoding"] == b"json"


def test_databricks_unity_catalog_key_queries_are_schema_scoped():
    source, server, uri, schema, _ = cloud_fixture("databricks")

    class CatalogCursor:
        def __init__(self):
            self.calls = []

        def execute(self, sql, parameters):
            self.calls.append((sql, parameters))

        def fetchall(self):
            if len(self.calls) == 1:
                return [("id",)]
            return [("items_parent", "parent", "id", "id")]

    cur = CatalogCursor()
    assert source._keys(cur, schema, "items") == (["id"], [("items_parent", "parent", "id", "id")])
    assert all(parameters == ("demo", "items") for _, parameters in cur.calls)
    assert "information_schema.table_constraints" in cur.calls[0][0]
    assert "position_in_unique_constraint=pk.ordinal_position" in cur.calls[1][0]
