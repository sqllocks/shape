"""OneLake server token failures use the supported SQL write path, without data loss."""

import pytest
from shape_fabric import WarehouseWriter, WriteError
from shape_fabric.testing import (
    FakeSqlCursor,
    FakeSqlServer,
    MemoryFS,
    sample_batch,
    sample_batches,
)

from shape.errors import ShapeError

CS = "Server=wh.datawarehouse.fabric.microsoft.com;Database=warehouse"
STAGING = "onelake://workspace/lakehouse/Files"


def make():
    fs = MemoryFS()
    server = FakeSqlServer(fs)
    writer = WarehouseWriter(CS, STAGING, filesystem=fs, connect=server.connect)
    return writer, server, fs


def reject_copy(sql, params):
    if sql.startswith("COPY INTO"):
        raise RuntimeError("[SQL Server]Access token couldn't be fetched for storage path. (13840)")


@pytest.mark.parametrize("mode", ["create", "append", "truncate", "replace"])
@pytest.mark.parametrize("key", [False, True])
def test_server_token_failure_recovers_all_rows_and_removes_staging(mode, key):
    writer, server, fs = make()
    if mode != "create":
        writer.write_table("customer", sample_batches())
    server.fail = reject_copy
    with pytest.warns(RuntimeWarning, match="13840.*parameterized INSERT"):
        assert (
            writer.write_table(
                "customer",
                sample_batches(),
                write_mode=mode,
                chunk_rows=3,
                primary_key=["id"] if key else [],
            )
            == 7
        )
    assert len(server.rows("dbo", "customer")) == (14 if mode == "append" else 7)
    assert any(sql.startswith("INSERT INTO") for sql in server.statements)
    assert fs.files == {}


@pytest.mark.parametrize("message", ["other failure 13841", "unrelated row error 13840"])
def test_unrelated_copy_failure_is_not_retried_as_insert(message):
    writer, server, fs = make()

    def fail(sql, params):
        if sql.startswith("COPY INTO"):
            raise RuntimeError(message)

    server.fail = fail
    with pytest.raises(WriteError, match=message):
        writer.write_table("customer", sample_batches())
    assert not any(sql.startswith("INSERT INTO") for sql in server.statements)
    assert fs.files == {}
    assert ("dbo", "customer") not in server.tables


@pytest.mark.parametrize("mode", ["create", "append", "truncate", "replace"])
def test_failed_insert_recovery_rolls_back_and_removes_staging(mode):
    writer, server, fs = make()
    if mode != "create":
        writer.write_table("customer", sample_batches())
    previous = server.rows("dbo", "customer") if mode != "create" else None

    def fail(sql, params):
        reject_copy(sql, params)
        if sql.startswith("INSERT INTO"):
            raise RuntimeError("insert recovery failed")

    server.fail = fail
    with (
        pytest.warns(RuntimeWarning, match="13840"),
        pytest.raises(WriteError, match="insert recovery failed"),
    ):
        writer.write_table("customer", sample_batches(), write_mode=mode, chunk_rows=3)
    assert fs.files == {}
    if previous is None:
        assert ("dbo", "customer") not in server.tables
    else:
        assert server.rows("dbo", "customer") == previous


def test_recovery_keeps_sql_batches_bounded(monkeypatch):
    writer, server, fs = make()
    server.fail = reject_copy
    sizes = []
    original = FakeSqlCursor.executemany

    def record(cursor, sql, params):
        sizes.append(len(params))
        return original(cursor, sql, params)

    monkeypatch.setattr(FakeSqlCursor, "executemany", record)
    with pytest.warns(RuntimeWarning, match="13840"):
        assert writer.write_table("customer", [sample_batch(n=12001)]) == 12001
    assert sizes == [5000, 5000, 2001]
    assert fs.files == {}


def test_the_fallback_is_visible_on_stderr_and_in_the_result_summary(capsys):
    writer, server, _fs = make()
    server.fail = reject_copy
    with pytest.warns(RuntimeWarning, match="13840"):
        result = writer.write_tables({"customer": sample_batches()})
    note = result.notes[0]
    assert "dbo.customer" in note and "13840" in note and "7 rows" in note and "slower" in note
    assert f"WARNING: {note}" in capsys.readouterr().err
    assert f"WARNING: {note}" in result.summary()
    assert writer.notes == []  # drained into the result


def test_the_fallback_gives_up_with_the_cause_when_it_runs_past_its_limit(monkeypatch):
    writer, server, fs = make()
    server.fail = reject_copy
    monkeypatch.setenv("SHAPE_WAREHOUSE_INSERT_SECONDS", "0")
    with pytest.warns(RuntimeWarning, match="13840"), pytest.raises(ShapeError) as err:
        writer.write_table("customer", sample_batches())
    assert "stopped after 0 s" in str(err.value) and "13840" in str(err.value)
    assert fs.files == {}  # staging removed
    with pytest.raises(KeyError):  # the table this call created is dropped again
        server.rows("dbo", "customer")
