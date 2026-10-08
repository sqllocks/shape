"""Item 6 with DuckDB and Ibis installed."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest
from shape_integrations import duckdb_source as ds
from shape_integrations.testing import write_tables

from shape.plugins import kit

duckdb = pytest.importorskip("duckdb", reason="needs the 'ibis' extra")
pytestmark = pytest.mark.integration


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "shape.duckdb"
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE people AS SELECT i AS id, 'name' || i AS name, i * 1.5 AS score, "
        "TIMESTAMP '2026-01-01 00:00:00' + INTERVAL (i) DAY AS seen, "
        "CAST(i AS DECIMAL(10, 2)) AS amount FROM range(1000) t(i)"
    )
    con.execute("CREATE TABLE empty_t (a INTEGER, b VARCHAR)")
    con.execute('CREATE TABLE "we""ird" AS SELECT 1 AS x')
    con.execute("CREATE SCHEMA s1")
    con.execute("CREATE TABLE s1.inner_t AS SELECT 7 AS v")
    con.close()
    return path


def uri(db, table="people"):
    return f"duckdb://{db}?table={table}"


def test_it_passes_the_conformance_kit(db):
    kit.check_source(ds.DuckDbSource(), uri(db))


def test_rows_and_types_come_through_arrow(db):
    s = ds.DuckDbSource()
    schema = s.schema(uri(db))
    assert schema.names == ["id", "name", "score", "seen", "amount"]
    table = pa.Table.from_batches(list(s.read(uri(db))), schema=schema)
    assert table.num_rows == 1000
    assert pa.types.is_integer(schema.field("id").type)
    assert pa.types.is_timestamp(schema.field("seen").type)
    assert pa.types.is_decimal(schema.field("amount").type)
    row = table.slice(3, 1).to_pylist()[0]
    assert row["id"] == 3 and row["name"] == "name3" and row["score"] == 4.5
    assert row["seen"] == datetime(2026, 1, 4) and row["amount"] == Decimal("3.00")


def test_batches_respect_batch_size(db):
    batches = list(ds.DuckDbSource().read(uri(db), batch_size=300))
    assert sum(b.num_rows for b in batches) == 1000
    assert len(batches) >= 4 and all(b.num_rows <= 300 for b in batches)


def test_an_empty_table_has_a_schema_and_no_rows(db):
    s = ds.DuckDbSource()
    assert s.schema(uri(db, "empty_t")).names == ["a", "b"]
    assert sum(b.num_rows for b in s.read(uri(db, "empty_t"))) == 0


def test_a_schema_qualified_table_and_an_odd_name_are_read(db):
    s = ds.DuckDbSource()
    assert list(s.read(uri(db, "s1.inner_t")))[0].to_pylist() == [{"v": 7}]
    assert list(s.read(uri(db, "we%22ird")))[0].to_pylist() == [{"x": 1}]


def test_a_missing_table_names_the_ones_there_are(db):
    with pytest.raises(ValueError, match="no table 'nope'") as info:
        ds.DuckDbSource().schema(uri(db, "nope"))
    assert "people" in str(info.value)


def test_a_table_name_cannot_inject_sql(db):
    s = ds.DuckDbSource()
    with pytest.raises(ValueError, match="no table"):
        s.schema(uri(db, "people%22%3B%20DROP%20TABLE%20people%3B%20--"))
    assert sum(b.num_rows for b in s.read(uri(db))) == 1000


def test_the_connection_is_read_only(db, monkeypatch):
    seen = []
    real = duckdb.connect

    def spy(*args, **kwargs):
        seen.append(kwargs.get("read_only"))
        return real(*args, **kwargs)

    monkeypatch.setattr(duckdb, "connect", spy)
    ds.DuckDbSource().schema(uri(db))
    list(ds.DuckDbSource().read(uri(db)))
    assert seen and all(flag is True for flag in seen)


def test_the_file_is_untouched_and_can_be_read_while_another_reader_has_it_open(db):
    before = (db.stat().st_size, db.stat().st_mtime_ns)
    other = duckdb.connect(str(db), read_only=True)
    try:
        assert sum(b.num_rows for b in ds.DuckDbSource().read(uri(db))) == 1000
    finally:
        other.close()
    assert (db.stat().st_size, db.stat().st_mtime_ns) == before


def test_a_generator_that_is_dropped_early_closes_the_connection(db):
    gen = ds.DuckDbSource().read(uri(db), batch_size=10)
    next(gen)
    gen.close()
    writer = duckdb.connect(str(db))  # would fail if a reader still held the file open
    writer.close()


def test_the_core_profiler_reads_the_uri_through_the_plugin(db):
    from shape.profile.reference.sources import _remote_table

    name, table = _remote_table(uri(db), None)
    assert table.num_rows == 1000 and name == "shape"


class TestIbis:
    @pytest.fixture(autouse=True)
    def _library(self):
        pytest.importorskip("ibis", reason="needs the 'ibis' extra")

    def test_every_table_of_an_output_directory_is_a_view(self, tmp_path):
        from shape_integrations import ibis as sibis

        out = write_tables(tmp_path / "out", rows=40)
        pq.write_table(pa.table({"k": [1, 2, 3]}), out / "keys.parquet")
        (out / "events.csv").write_text("id,kind\n1,a\n2,b\n")
        (out / "log.jsonl").write_text(json.dumps({"n": 1}) + "\n" + json.dumps({"n": 2}) + "\n")
        con = sibis.connect(out)
        assert sorted(con.list_tables()) == ["events", "keys", "log", "orders", "people"]
        assert con.table("people").count().execute() == 40
        assert con.table("events").count().execute() == 2
        assert con.table("log").count().execute() == 2
        assert list(con.table("people").columns) == ["age", "city", "income"]
        joined = con.table("orders").qty.sum().execute()
        assert joined > 0

    def test_it_returns_an_ibis_duckdb_connection(self, tmp_path):
        from shape_integrations import ibis as sibis

        con = sibis.connect(write_tables(tmp_path / "o", rows=5, extra=False))
        assert type(con).__module__.startswith("ibis.backends.duckdb")

    def test_a_table_in_two_formats_is_refused(self, tmp_path):
        from shape_integrations import ibis as sibis

        out = write_tables(tmp_path / "o", rows=5, extra=False)
        (out / "people.csv").write_text("a\n1\n")
        with pytest.raises(ValueError, match="people"):
            sibis.connect(out)

    def test_a_missing_or_empty_directory_is_refused(self, tmp_path):
        from shape_integrations import ibis as sibis

        with pytest.raises(FileNotFoundError):
            sibis.connect(tmp_path / "nope")
        (tmp_path / "empty").mkdir()
        with pytest.raises(ValueError, match="no Parquet, CSV or JSONL"):
            sibis.connect(tmp_path / "empty")
        (tmp_path / "f.parquet").write_bytes(b"")
        with pytest.raises(NotADirectoryError):
            sibis.connect(tmp_path / "f.parquet")

    def test_a_file_name_with_odd_characters_is_a_usable_table_name(self, tmp_path):
        from shape_integrations import ibis as sibis

        out = tmp_path / "o"
        out.mkdir()
        pq.write_table(pa.table({"a": [1]}), out / "my-table.v2.parquet")
        assert sibis.connect(out).table("my-table.v2").count().execute() == 1
