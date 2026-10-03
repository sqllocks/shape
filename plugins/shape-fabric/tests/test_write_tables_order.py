"""write_tables checks order against the tables before writing anything (#458)."""

from __future__ import annotations

import pyarrow as pa
import pytest
from shape_fabric.sqldb import SqlDatabaseWriter
from shape_fabric.testing import FakeSqlServer

from shape.errors import ShapeError

CS = "Driver={ODBC Driver 18 for SQL Server};Server=h;Database=d"


def test_a_missing_table_in_order_is_refused_before_any_write() -> None:
    server = FakeSqlServer()
    batch = pa.record_batch({"id": pa.array([1])})
    with (
        SqlDatabaseWriter(CS, connection=server.connect(CS)) as writer,
        pytest.raises(ShapeError, match="'b'"),
    ):
        writer.write_tables({"a": [batch]}, order=["a", "b"])
    assert server.tables == {}
