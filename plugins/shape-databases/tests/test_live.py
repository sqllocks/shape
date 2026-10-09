"""Round trips against real Snowflake and Databricks accounts (``live``).

    SHAPE_TEST_SNOWFLAKE_URI='snowflake://user@account/DB/SCHEMA?warehouse=WH' \\
    SNOWFLAKE_PASSWORD=... \\
    SHAPE_TEST_DATABRICKS_URI='databricks://host/sql/1.0/warehouses/id?catalog=main&schema=demo' \\
    DATABRICKS_TOKEN=... \\
        pytest -m live plugins/shape-databases/tests/test_live.py

Each test skips, naming the missing setting, when its connection settings are absent. A test
writes a table with a unique name, reads it back, compares the row count and the dataset id
(``shape.repro.dataset_id``) of what was written with what was read, and drops the table.
Snowflake also accepts ``SHAPE_TEST_SNOWFLAKE_PRIVATE_KEY`` (a ``file://`` or ``env://``
reference) instead of a password.
"""

import os
import uuid

import pyarrow as pa
import pytest
from shape_databases import DatabricksSink, SnowflakeSink
from shape_databases.testing import SAMPLE_SCHEMA, sample_batch

from shape.repro import dataset_id

pytestmark = pytest.mark.live


def need(*names):
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        pytest.skip(f"live test needs {', '.join(missing)}")
    return [os.environ[n] for n in names]


def _plain(value):
    return bytes(value) if isinstance(value, bytearray) else value


def _table(rows, names):
    columns = {n: [_plain(r[i]) for r in rows] for i, n in enumerate(names)}
    return pa.Table.from_pydict(columns, schema=SAMPLE_SCHEMA)


def _read_back(sink, uri, table, options):
    plan = sink.plan(uri, table, options)
    conn = sink.default_connect(**sink.connect_params(plan))
    try:
        cur = conn.cursor()
        quoted = sink_quote(sink, table)
        cur.execute(f"SELECT * FROM {quoted} ORDER BY 1")  # nosec B608
        return [tuple(r) for r in cur.fetchall()]
    finally:
        conn.close()


def sink_quote(sink, name):
    from shape_databases import _sql

    return _sql.quote(name, sink.dialect)


def _drop(sink, uri, table, options):
    plan = sink.plan(uri, table, options)
    conn = sink.default_connect(**sink.connect_params(plan))
    try:
        conn.cursor().execute(f"DROP TABLE IF EXISTS {sink_quote(sink, table)}")
    finally:
        conn.close()


def _round_trip(sink, uri, options):
    table = f"shape_live_{uuid.uuid4().hex[:8]}"
    batches = [sample_batch(0, 30), sample_batch(30, 12)]
    written = pa.Table.from_batches(batches)
    try:
        assert sink.write(uri, table, iter(batches), commit_rows=30, **options) == 42
        rows = _read_back(sink, uri, table, options)
        assert len(rows) == written.num_rows
        got = _table(rows, SAMPLE_SCHEMA.names)
        assert dataset_id({table: got}) == dataset_id({table: written})
    finally:
        _drop(sink, uri, table, options)


def test_snowflake_round_trip():
    (uri,) = need("SHAPE_TEST_SNOWFLAKE_URI")
    options = {}
    if os.environ.get("SHAPE_TEST_SNOWFLAKE_PRIVATE_KEY"):
        options["private_key"] = os.environ["SHAPE_TEST_SNOWFLAKE_PRIVATE_KEY"]
    else:
        need("SNOWFLAKE_PASSWORD")
    _round_trip(SnowflakeSink(), uri, options)


def test_databricks_round_trip():
    (uri, _) = need("SHAPE_TEST_DATABRICKS_URI", "DATABRICKS_TOKEN")
    _round_trip(DatabricksSink(), uri, {})


@pytest.mark.parametrize("dialect", ["snowflake", "databricks"])
def test_w9_03_live_width_decimal_and_zoned_instant(dialect):
    import datetime as dt
    from decimal import Decimal

    from shape_databases import _sql

    if dialect == "snowflake":
        uri = need("SHAPE_TEST_SNOWFLAKE_URI")[0]
        if not (
            os.environ.get("SNOWFLAKE_PASSWORD")
            or os.environ.get("SHAPE_TEST_SNOWFLAKE_PRIVATE_KEY")
        ):
            pytest.skip("live test needs SNOWFLAKE_PASSWORD or SHAPE_TEST_SNOWFLAKE_PRIVATE_KEY")
        sink = SnowflakeSink()
        options = (
            {"private_key": os.environ["SHAPE_TEST_SNOWFLAKE_PRIVATE_KEY"]}
            if os.environ.get("SHAPE_TEST_SNOWFLAKE_PRIVATE_KEY")
            else {}
        )
    else:
        uri = need("SHAPE_TEST_DATABRICKS_URI", "DATABRICKS_TOKEN")[0]
        sink = DatabricksSink()
        options = {}
    name = "w903_" + uuid.uuid4().hex[:12]
    schema = pa.schema(
        [("small", pa.int16()), ("amount", pa.decimal128(12, 4)), ("at", pa.timestamp("us", "UTC"))]
    )
    value = dt.datetime(2024, 1, 2, 3, 4, 5, 999999, tzinfo=dt.timezone(dt.timedelta(hours=9)))
    batch = pa.RecordBatch.from_pydict(
        {
            "small": [-32768, 32767],
            "amount": [Decimal("-99999999.9999"), Decimal("0.0001")],
            "at": [value, None],
        },
        schema=schema,
    )
    plan = sink.plan(uri, name, options)
    conn = sink.default_connect(**sink.connect_params(plan))
    try:
        sink.write(uri, name, [batch], **options)
        cur = conn.cursor()
        quoted = _sql.qualified(plan.schema_name, name, dialect)
        cur.execute(f"SELECT * FROM {quoted} ORDER BY 1")  # nosec B608 - checked quoted identifiers
        rows = cur.fetchall()
        read = pa.Table.from_pylist(
            [dict(zip(schema.names, r, strict=True)) for r in rows], schema=schema
        )
        assert read["at"][0].as_py() == value.astimezone(dt.UTC)
        assert dataset_id({"items": read}) == dataset_id({"items": pa.Table.from_batches([batch])})
    finally:
        cur = conn.cursor()
        cur.execute(_sql.drop_table_sql(plan.schema_name, name, dialect))
        cur.close()
        conn.close()
