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


@pytest.mark.emulator
@pytest.mark.parametrize(
    "cls, setting, default",
    [
        (PostgresSource, "SHAPE_TEST_POSTGRES", "postgresql://shape@localhost:5432/shape"),
        (MySqlSource, "SHAPE_TEST_MYSQL", "mysql://shape@localhost:3306/shape"),
    ],
)
def test_emulator_declared_catalog_types_and_relationships(cls, setting, default):
    source = cls()
    uri = os.environ.get(setting, default)
    plan = source._plan(uri, {}, False)
    suffix = uuid.uuid4().hex[:12]
    parent, child = "shape_parent_" + suffix, "shape_child_" + suffix
    dialect = source.sink.dialect

    def quote(name):
        return _sql.quote(name, dialect)

    with source._connection(plan) as conn:
        cur = conn.cursor()
        schema = source._schema_name(cur, plan)
        parent_name = _sql.qualified(schema, parent, dialect)
        child_name = _sql.qualified(schema, child, dialect)
        timestamp = "TIMESTAMPTZ" if dialect == "postgres" else "DATETIME(6)"
        binary = "BYTEA" if dialect == "postgres" else "BLOB"
        boolean = "BOOLEAN" if dialect == "postgres" else "TINYINT(1)"
        try:
            cur.execute(
                f"CREATE TABLE {parent_name} ("  # nosec B608
                f"{quote('id')} SMALLINT PRIMARY KEY, "
                f"{quote('amount')} NUMERIC(12,4) NOT NULL, "
                f"{quote('seen')} {timestamp}, {quote('payload')} {binary}, "
                f"{quote('active')} {boolean})"
            )
            cur.execute(
                f"CREATE TABLE {child_name} ("  # nosec B608
                f"{quote('id')} INTEGER PRIMARY KEY, {quote('parent_id')} SMALLINT NOT NULL, "
                f"FOREIGN KEY ({quote('parent_id')}) REFERENCES {parent_name} ({quote('id')}))"
            )
            cur.execute(
                f"INSERT INTO {parent_name} ({quote('id')}, {quote('amount')}) "  # nosec B608
                "VALUES (%s, %s)",
                (7, "12.3400"),
            )
            cur.execute(
                f"INSERT INTO {child_name} VALUES (%s, %s)",  # nosec B608
                (11, 7),
            )
            for name in (parent_name, child_name):
                cur.execute(f"ANALYZE {'TABLE ' if dialect == 'mysql' else ''}{name}")
                if dialect == "mysql":
                    cur.fetchall()
            conn.commit()
            prof = source.profile_database(uri, tables=[parent, child], sample_rows=1)
            assert prof.tables[parent]["primary_key"] == ["id"]
            assert prof.tables[child]["primary_key"] == ["id"]
            assert prof.tables[parent]["row_count"] >= 1
            assert prof.tables[parent]["sampled_rows"] == 1
            columns = prof.tables[parent]["columns"]
            assert columns["amount"]["nullable"] is False
            assert columns["seen"]["nullable"] is True
            read_schema = source._columns(cur, schema, parent)
            assert read_schema.field("id").type == pa.int16()
            assert read_schema.field("amount").type == pa.decimal128(12, 4)
            assert read_schema.field("payload").type == pa.binary()
            assert read_schema.field("active").type == pa.bool_()
            assert read_schema.field("seen").type == pa.timestamp(
                "us", tz="UTC" if dialect == "postgres" else None
            )
            relations = prof.to_dict()["relationships"]
            assert len(relations) == 1
            assert relations[0]["parent"] == parent
            assert relations[0]["child"] == child
            assert relations[0]["parent_columns"] == ["id"]
            assert relations[0]["child_columns"] == ["parent_id"]
            assert relations[0]["evidence"] == "declared"
        finally:
            # Child first preserves the very foreign key this fixture exercises.
            conn.rollback()
            for table in (child, parent):
                cur.execute(_sql.drop_table_sql(schema, table, dialect))
            conn.commit()
            cur.close()


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
        assert os.environ.get(setting), f"live test needs {setting}"
    roundtrip(cls(), os.environ[uri_setting])
