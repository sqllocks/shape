"""One deterministic table that exercises every writer type, and its schema metadata."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pyarrow as pa

COLUMNS = {
    "id": {"type": "integer", "nullable": False},
    "name": {"type": "string", "nullable": True, "max_length": 40},
    "price": {"type": "decimal", "nullable": False, "precision": 10, "scale": 2},
    "ratio": {"type": "float", "nullable": True},
    "active": {"type": "boolean", "nullable": False},
    "born": {"type": "date", "nullable": True},
    "seen": {"type": "timestamp", "nullable": True},
}


def table() -> pa.Table:
    return pa.table(
        {
            "id": pa.array([1, 2, 3], pa.int64()),
            "name": pa.array(["plain", "O'Brien \\ é", None], pa.string()),
            "price": pa.array(
                [Decimal("1.50"), Decimal("0.00"), Decimal("12345.67")], pa.decimal128(10, 2)
            ),
            "ratio": pa.array([0.5, None, 2.0], pa.float64()),
            "active": pa.array([True, False, True], pa.bool_()),
            "born": pa.array([dt.date(2020, 2, 29), None, dt.date(1999, 12, 31)], pa.date32()),
            "seen": pa.array(
                [
                    dt.datetime(2025, 1, 2, 3, 4, 5, 678000),
                    None,
                    dt.datetime(2025, 12, 31, 23, 59, 59),
                ],
                pa.timestamp("us"),
            ),
        }
    )
