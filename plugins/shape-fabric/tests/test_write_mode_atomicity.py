"""#429: what a failed write leaves behind, per write mode, as documented.

``truncate`` and ``replace`` commit together with the rows (the owner-approved fix of #429), so a
failed write keeps the old rows of an existing table; a table the call created is dropped again.
These tests pin the documented statement to what the fake service does, so the docs cannot claim
more than the writers deliver.
"""

from pathlib import Path

import pyarrow as pa
import pytest
from shape_fabric import SqlDatabaseWriter, WarehouseWriter, WriteError, sqldb, warehouse
from shape_fabric.testing import FakeSqlServer, MemoryFS, sample_batches

from shape.errors import ShapeError

CS = (
    "Driver={ODBC Driver 18 for SQL Server};Server=db.example.test;Database=d;"
    "UID=u;PWD=hunter2hunter2"
)
STAGING = "onelake://ws/lh/Files/stage"
DOCS = Path(__file__).resolve().parents[3] / "docs" / "plugins" / "fabric-writers.md"
CLAIM = "old rows"


def _bad_tail():
    # a second batch whose columns differ: the write fails after the first batch was inserted
    return [*sample_batches()[:1], pa.RecordBatch.from_arrays([pa.array([1])], names=["x"])]


def _seeded():
    server = FakeSqlServer()
    SqlDatabaseWriter(CS, connect=server.connect).write_table("t", sample_batches())
    return server


@pytest.mark.parametrize(
    ("mode", "table_survives", "rows_left"),
    [("create", False, 0), ("append", True, 7), ("truncate", True, 7), ("replace", True, 7)],
)
def test_sql_failed_write_leaves_what_the_docs_say(mode, table_survives, rows_left):
    server = _seeded() if mode != "create" else FakeSqlServer()
    w = SqlDatabaseWriter(CS, connect=server.connect)
    with pytest.raises(ShapeError, match="a batch has columns"):
        w.write_table("t", _bad_tail(), write_mode=mode)
    assert (("dbo", "t") in server.tables) is table_survives
    if table_survives:
        assert len(server.rows("dbo", "t")) == rows_left


@pytest.mark.parametrize(
    ("mode", "table_survives", "rows_left"),
    [("create", False, 0), ("append", True, 7), ("truncate", True, 7), ("replace", True, 7)],
)
def test_warehouse_failed_write_leaves_what_the_docs_say(mode, table_survives, rows_left):
    fs = MemoryFS()
    server = FakeSqlServer(fs)
    seed = WarehouseWriter(CS, STAGING, connect=server.connect, filesystem=fs, run_id="run1")
    if mode != "create":
        seed.write_table("t", sample_batches())
    w = WarehouseWriter(CS, STAGING, connect=server.connect, filesystem=fs, run_id="run2")
    with pytest.raises((ShapeError, WriteError)):
        w.write_table("t", _bad_tail(), write_mode=mode)
    assert (("dbo", "t") in server.tables) is table_survives
    if table_survives:
        assert len(server.rows("dbo", "t")) == rows_left


@pytest.mark.parametrize("where", ["sqldb", "warehouse", "fabric-writers.md"])
def test_the_documentation_states_that_a_failed_write_keeps_the_old_rows(where):
    text = {
        "sqldb": sqldb.__doc__,
        "warehouse": warehouse.__doc__,
        "fabric-writers.md": DOCS.read_text("utf-8"),
    }[where]
    flat = " ".join((text or "").split())
    assert CLAIM in flat
    assert "truncate" in flat and "replace" in flat
