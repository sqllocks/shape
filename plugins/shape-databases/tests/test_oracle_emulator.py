"""Oracle Free 23 acceptance; explicitly selected emulator tests never skip missing service."""

import os
import uuid
from decimal import Decimal

import pyarrow as pa
import pytest
from shape_databases import OracleSink, OracleSource

from shape.errors import ShapeError
from shape.repro import dataset_id

pytestmark = pytest.mark.emulator
URI = os.environ.get("SHAPE_TEST_ORACLE", "oracle://shape@localhost:1521/FREEPDB1")


@pytest.fixture
def table():
    name = "Oracle_" + uuid.uuid4().hex[:12]
    yield name
    sink = OracleSink()
    plan = sink.plan(URI, name, {})
    source = OracleSource()
    with source._connection(plan) as conn:
        cur = conn.cursor()
        try:
            cur.execute(f'DROP TABLE "{name}"')
        finally:
            cur.close()


def test_oracle_exact_types_lob_nanoseconds_and_catalog(table):
    batch = pa.record_batch(
        [
            pa.array([Decimal("12345678901234567890.1234")], pa.decimal128(24, 4)),
            pa.array(["2026-01-01T00:00:00.123456789Z"]).cast(pa.timestamp("ns", "UTC")),
            pa.array([b"payload"]),
            pa.array([True]),
        ],
        names=["amount", "at", "payload", "active"],
    )
    assert OracleSink().write(URI, table, [batch]) == 1
    source = OracleSource()
    got = pa.Table.from_batches(source.read(URI + f"?table={table}"))
    assert got.column("amount")[0].as_py() == Decimal("12345678901234567890.1234")
    assert (
        got.column("at").cast(pa.int64()).to_pylist()
        == batch.column(1).cast(pa.int64()).to_pylist()
    )
    assert got.column("payload").to_pylist() == [b"payload"]
    profile = source.profile_database(URI + "?exact_counts=true", tables=[table], sample_rows=1)
    assert profile.tables[table]["row_count"] == 1
    assert profile.provenance["read_path"] == "cursor"


def test_oracle_empty_strings_and_nullable_conversion(table):
    batch = pa.record_batch({"text": [""]})
    with pytest.raises(ShapeError, match="empty"):
        OracleSink().write(URI, table, [batch])
    assert OracleSink().write(URI, table, [batch], empty_string="null") == 1
    got = pa.Table.from_batches(OracleSource().read(URI + f"?table={table}"))
    assert got.column("text").to_pylist() == [None]


def test_oracle_generated_dataset_identity_and_declared_key(table):
    from shape import generate
    from shape.generation.schema import Column, Generation, GenSchema, Model, Table

    schema = GenSchema(
        Model(name="oracle", seed=7),
        {
            table: Table(
                table,
                {
                    "id": Column("id", "string", {"strategy": "uuid"}),
                    "value": Column("value", "float", {"strategy": "uniform", "low": 0, "high": 1}),
                },
                ["id"],
            )
        },
        generation=Generation(scales={"small": {table: 11}}),
    )
    generated = generate(schema, seed=7)
    expected = generated[table]
    batch = expected.to_batches()[0]
    assert (
        OracleSink().write(
            URI, table, [batch], primary_key=["id"], columns={"id": {"max_length": 36}}
        )
        == 11
    )
    got = pa.Table.from_batches(OracleSource().read(URI + f"?table={table}"))
    assert dataset_id({table: got}) == dataset_id({table: expected})
    profile = OracleSource().profile_database(URI + "?exact_counts=true", tables=[table])
    assert profile.tables[table]["primary_key"] == ["id"]
