"""W9-03 T-SQL type fidelity and UTC normalization."""

import pyarrow as pa
import pytest
from shape_fabric import _tsql
from shape_fabric.sqldb import SqlDatabaseWriter


@pytest.mark.parametrize("warehouse,zoned", [(False, "DATETIMEOFFSET(6)"), (True, "DATETIME2(6)")])
def test_wanted1_dialect_timestamp_and_width(warehouse, zoned):
    assert (
        _tsql.column_type(pa.field("at", pa.timestamp("us", "Asia/Tokyo")), warehouse=warehouse)
        == zoned
    )
    assert _tsql.column_type(pa.field("small", pa.int16()), warehouse=warehouse) == "SMALLINT"
    assert _tsql.column_type(pa.field("clock", pa.time32("s")), warehouse=warehouse) == "TIME(0)"


def test_wanted3_sql_database_normalization_keeps_utc_offset():
    batch = pa.RecordBatch.from_arrays(
        [pa.array([0, None], type=pa.timestamp("us", "Asia/Tokyo"))], names=["at"]
    )
    normalized = _tsql.normalize_batch(batch, warehouse=False)
    assert normalized.column(0).type == pa.timestamp("us", "UTC")
    assert normalized.column(0)[0].as_py().utcoffset().total_seconds() == 0
    assert normalized.column(0)[1].as_py() is None
    assert _tsql.normalize_batch(batch, warehouse=True).column(0).type == pa.timestamp("us")


def test_wanted3_create_ddl_keeps_zone_before_normalization():
    writer = SqlDatabaseWriter("Server=localhost;Database=shape", warehouse=False)
    ddl = writer.create_ddl("rows", pa.schema([("at", pa.timestamp("us", "Asia/Tokyo"))]))
    assert "DATETIMEOFFSET(6)" in ddl


def test_wanted2_warehouse_unbounded_strings_and_synapse_limit():
    from shape.errors import ShapeError

    field = pa.field("text", pa.string())
    assert _tsql.column_type(field, warehouse=True) == "VARCHAR(MAX)"
    assert _tsql.column_type(field, {"max_length": 9000}, warehouse=True) == "VARCHAR(MAX)"
    assert _tsql.column_type(field, warehouse=True, synapse=True) == "VARCHAR(8000)"
    with pytest.raises(ShapeError, match="8000"):
        _tsql.column_type(field, {"max_length": 9000}, warehouse=True, synapse=True)


def test_wanted3_odbc_uint64_and_microsecond_time_parameters():
    import datetime as dt
    from decimal import Decimal

    batch = pa.RecordBatch.from_arrays(
        [
            pa.array([2**64 - 1, None], type=pa.uint64()),
            pa.array([dt.time(23, 59, 59, 999999), None], type=pa.time64("us")),
        ],
        names=["unsigned", "clock"],
    )
    normalized = _tsql.normalize_batch(batch, warehouse=False)
    rows = _tsql.rows_as_params(normalized)
    assert isinstance(rows[0][0], Decimal)
    assert rows[0][0] == Decimal(2**64 - 1)
    assert rows[0][1] == "23:59:59.999999"
    assert rows[1] == (None, None)
