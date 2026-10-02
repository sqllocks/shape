"""WarehouseWriter: staging in OneLake, COPY INTO, cleanup, verification and SQL safety."""

import io
import warnings

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from shape_fabric import WarehouseWriter, WriteError
from shape_fabric.testing import FakeSqlServer, MemoryFS, sample_batch, sample_schema
from shape_fabric.warehouse import chunked, copy_into_sql, copy_literal, staging_slug

from shape.errors import ShapeError

CS = "Driver={ODBC Driver 18 for SQL Server};Server=w.datawarehouse.fabric.microsoft.com;Database=wh;UID=u;PWD=pw"
STAGING = "onelake://Analytics/Sales/Files"
HTTPS = "https://onelake.dfs.fabric.microsoft.com/Analytics/Sales.Lakehouse/Files/staging/run000000001"


def make(**kw):
    fs = MemoryFS()
    server = FakeSqlServer(fs)
    w = WarehouseWriter(
        CS, STAGING, connect=server.connect, filesystem=fs, run_id="run000000001", **kw
    )
    return w, server, fs


def test_stages_parquet_then_copies_and_cleans_up(batches):
    w, server, fs = make()
    assert w.write_table("customer", batches, chunk_rows=5) == 7
    assert len(server.rows("dbo", "customer")) == 7
    copy = next(s for s in server.statements if s.startswith("COPY INTO"))
    assert copy == (
        f"COPY INTO [dbo].[customer] FROM '{HTTPS}/customer-{staging_slug('customer')[-8:]}/' "
        "WITH (FILE_TYPE = 'PARQUET')"
    )
    assert fs.files == {}  # staging removed
    assert any("chunk_000001.parquet" in k for k in fs.opened)  # 7 rows at 5 per file: two chunks


def test_warehouse_types_are_used_and_a_connection_to_a_warehouse_is_assumed(batches):
    w, server, _ = make()
    w.write_table("customer", batches)
    ddl = next(s for s in server.statements if s.startswith("CREATE TABLE"))
    assert "VARCHAR(8000)" in ddl and "NVARCHAR" not in ddl and "DATETIME2(6)" in ddl


def test_staged_timestamps_are_microseconds_the_warehouse_cannot_read_nanoseconds(batches):
    w, server, fs = make()
    seen = {}
    orig = fs.rm

    def keep(path, recursive=False):  # look at the staged file before it is removed
        for key, data in fs.files.items():
            seen[key] = pq.read_table(io.BytesIO(data)).schema
        orig(path, recursive)

    fs.rm = keep
    w.write_table("customer", batches)
    (schema,) = seen.values()
    assert schema.field("seen").type == pa.timestamp("us")
    assert schema.field("seen_tz").type == pa.timestamp("us")
    assert schema.field("segment").type == pa.string()  # dictionaries decoded


def test_staging_is_removed_when_the_copy_fails(batches):
    w, server, fs = make()

    def fail(sql, params):
        if sql.startswith("COPY INTO"):
            raise RuntimeError("Bulk load failed")

    server.fail = fail
    with pytest.raises(WriteError, match="Bulk load failed"):
        w.write_table("customer", batches)
    assert fs.files == {}
    assert ("dbo", "customer") not in server.tables  # created by this call, dropped again


def test_staging_is_removed_when_the_source_fails_midway():
    w, server, fs = make()

    def gen():
        yield sample_batch(0, 5)
        raise RuntimeError("source died")

    with pytest.raises(WriteError, match="source died"):
        w.write_table("t", gen(), chunk_rows=3)
    assert fs.files == {} and ("dbo", "t") not in server.tables


def test_a_copy_that_loads_fewer_rows_than_were_staged_fails(batches):
    w, server, fs = make()
    server.copy_loads_fewer = 2
    with pytest.raises(ShapeError, match="loaded 5 of the 7 staged rows"):
        w.write_table("customer", batches)
    assert ("dbo", "customer") not in server.tables and fs.files == {}


def test_a_driver_without_a_rowcount_is_checked_by_counting_rows(batches):
    w, server, _ = make()
    server.copy_reports_rowcount = False
    assert w.write_table("customer", batches, write_mode="create") == 7
    w.write_table("customer", batches, write_mode="append")  # before/after counts: 7 more
    assert len(server.rows("dbo", "customer")) == 14
    # a driver that says -1 and a count that does not add up still fails
    server.copy_loads_fewer = 1
    with pytest.raises(ShapeError, match="loaded 6 of the 7"):
        w.write_table("customer", batches, write_mode="append")


def test_write_modes_match_the_sql_writer(batches):
    w, server, _ = make()
    w.write_table("t", batches)
    with pytest.raises(WriteError, match="already exists"):
        w.write_tables({"t": batches})
    w.write_table("t", batches, write_mode="append")
    assert len(server.rows("dbo", "t")) == 14
    w.write_table("t", batches[:1], write_mode="truncate")
    assert len(server.rows("dbo", "t")) == 4
    w.write_table("t", batches[1:], write_mode="replace")
    assert len(server.rows("dbo", "t")) == 3


def test_primary_key_is_declared_not_enforced(batches):
    w, server, _ = make()
    w.write_table("t", batches, primary_key=["id"])
    ddl = next(s for s in server.statements if s.startswith("CREATE TABLE"))
    assert "PRIMARY KEY NONCLUSTERED ([id]) NOT ENFORCED" in ddl


def test_staging_path_is_required_and_must_be_onelake():
    with pytest.raises(ShapeError, match="needs staging_path"):
        WarehouseWriter(CS, None)
    with pytest.raises(ShapeError, match="OneLake path"):
        WarehouseWriter(CS, "/tmp/staging")
    with pytest.raises(ShapeError, match="not a OneLake URI"):
        WarehouseWriter(CS, "abfss://c@acct.dfs.core.windows.net/p")


def test_the_copy_location_cannot_inject_sql():
    ok = f"{HTTPS}/customer-1234abcd/"
    assert copy_literal(ok) == f"'{ok}'"
    for evil in (
        "https://onelake.dfs.fabric.microsoft.com/a/b'; DROP TABLE x;--",
        "https://onelake.dfs.fabric.microsoft.com/a/b'/",
        "https://onelake.dfs.fabric.microsoft.com/a/b\n/c",
        "https://onelake.dfs.fabric.microsoft.com/a/b;c",
        "https://onelake.dfs.fabric.microsoft.com/a/b--c",
        "https://onelake.dfs.fabric.microsoft.com/a/b/*c",
        "https://host/a$b",
        "file:///etc/passwd",
    ):
        with pytest.raises(ShapeError, match="not a usable staging location"):
            copy_literal(evil)
    assert copy_into_sql("s]x", "t]y", ok).startswith("COPY INTO [s]]x].[t]]y] FROM ")


def test_awkward_table_names_get_safe_staging_folders_and_stay_apart(batches):
    assert staging_slug("a b'c") != staging_slug("a_b_c")
    assert set(staging_slug("x; DROP--")) <= set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")
    w, server, fs = make()
    assert w.write_table("o'brien; DROP TABLE x;--", batches) == 7
    assert ("dbo", "o'brien; DROP TABLE x;--") in server.tables


def test_chunking_slices_batches_to_the_row_limit():
    groups = list(chunked([sample_batch(0, 4), sample_batch(4, 4)], 3))
    assert [sum(b.num_rows for b in g) for g in groups] == [3, 3, 2]
    assert [len(g) for g in groups] == [1, 2, 1]


def test_cleanup_failure_warns_but_does_not_hide_the_result(batches):
    w, server, fs = make()

    def broken(path, recursive=False):
        raise PermissionError("nope")

    fs.rm = broken
    with pytest.warns(RuntimeWarning, match="could not remove the staged files"):
        assert w.write_table("t", batches) == 7


def test_empty_table_is_created_without_staging():
    w, server, fs = make()
    assert w.write_table("e", [], schema=sample_schema()) == 0
    assert ("dbo", "e") in server.tables and fs.opened == []
    assert not any(s.startswith("COPY") for s in server.statements)


def test_no_warnings_on_a_clean_run(batches):
    w, _, _ = make()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        w.write_table("t", batches)
