"""Database source smoke/round-trip tests for nightly emulators and opt-in live hosts."""

import os
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pyarrow as pa
import pytest
from shape_databases import _sql
from shape_databases.sources import MySqlSource, PostgresSource
from shape_databases.testing import sample_batch

from shape.repro import dataset_id


def roundtrip(source, uri):
    table = "shape_source_" + uuid.uuid4().hex[:12]
    # Existing SQL sinks declare zone-less timestamps (write fidelity is W9-03).
    # Use a zone-less timestamp fixture; zoned catalog reads are tested independently.
    original = sample_batch()
    fields = pa.schema(
        [
            pa.field(
                f.name, pa.timestamp("us") if f.name == "seen" else f.type, nullable=f.nullable
            )
            for f in original.schema
        ]
    )
    batch = pa.Table.from_batches([original]).cast(fields).to_batches()[0]
    parts = urlsplit(uri)
    query = dict(parse_qsl(parts.query))
    schema = query.pop("schema", None)
    query.pop("table", None)
    sink_uri = urlunsplit(parts._replace(query=urlencode(query)))
    source_uri = urlunsplit(
        parts._replace(
            query=urlencode({**query, **({"schema": schema} if schema else {}), "table": table})
        )
    )
    source.sink.write(sink_uri, table, [batch], primary_key=["id"], schema_name=schema)
    try:
        read = pa.Table.from_batches(source.read(source_uri, batch_size=2))
        assert dataset_id({table: read}) == dataset_id({table: pa.Table.from_batches([batch])})
        prof = source.profile_database(source_uri, tables=[table])
        assert prof.tables[table]["primary_key"] == ["id"]
        assert prof.tables[table]["sampled_rows"] == batch.num_rows
    finally:
        plan = source.sink.plan(sink_uri, table, {"schema_name": schema} if schema else {})
        conn = source.sink.default_connect(**source.sink.connect_params(plan))
        try:
            cur = conn.cursor()
            cur.execute(_sql.drop_table_sql(plan.schema_name, table, source.sink.dialect))
            conn.commit()
        finally:
            conn.close()


@pytest.mark.emulator
@pytest.mark.parametrize(
    "cls, setting, default",
    [
        (PostgresSource, "SHAPE_TEST_POSTGRES", "postgresql://shape@localhost:5432/shape"),
        (MySqlSource, "SHAPE_TEST_MYSQL", "mysql://shape@localhost:3306/shape"),
    ],
)
def test_emulator_source_roundtrip(cls, setting, default):
    roundtrip(cls(), os.environ.get(setting, default))


@pytest.mark.live
@pytest.mark.parametrize(
    "cls, uri_setting, password_setting",
    [
        (PostgresSource, "SHAPE_TEST_POSTGRES_URI", "SHAPE_POSTGRES_PASSWORD"),
        (MySqlSource, "SHAPE_TEST_MYSQL_URI", "SHAPE_MYSQL_PASSWORD"),
    ],
)
def test_live_source_roundtrip(cls, uri_setting, password_setting):
    for setting in [uri_setting, password_setting]:
        if not os.environ.get(setting):
            pytest.skip(f"live test needs {setting}")
    roundtrip(cls(), os.environ[uri_setting])
