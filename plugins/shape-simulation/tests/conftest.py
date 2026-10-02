"""Small in-memory tables the simulator tests share (no domain generation, so they run fast)."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pyarrow as pa
import pytest


@pytest.fixture
def orders() -> pa.Table:
    """600 orders over 30 days (2024-01-01 .. 2024-01-30), with a nullable integer column."""
    rng = np.random.default_rng(5)
    n = 600
    start = dt.datetime(2024, 1, 1)
    when = [start + dt.timedelta(seconds=int(s)) for s in rng.integers(0, 30 * 86400, n)]
    return pa.table(
        {
            "order_id": pa.array(np.arange(1, n + 1), pa.int64()),
            "customer_id": pa.array(rng.integers(1, 80, n), pa.int64()),
            "promo_id": pa.array(
                [None if i % 4 == 0 else int(i % 9) for i in range(n)], pa.int64()
            ),
            "status": pa.array(rng.choice(["new", "paid", "shipped"], n).tolist(), pa.string()),
            "total": pa.array(np.round(rng.uniform(5, 500, n), 2), pa.float64()),
            "is_gift": pa.array((rng.random(n) < 0.2).tolist(), pa.bool_()),
            "ordered_at": pa.array(when, pa.timestamp("us")),
        }
    )


@pytest.fixture
def products() -> pa.Table:
    """A table with no time column at all."""
    return pa.table(
        {
            "product_id": pa.array(range(1, 101), pa.int64()),
            "name": pa.array([f"item {i}" for i in range(100)], pa.string()),
            "updated_count": pa.array([i % 5 for i in range(100)], pa.int64()),
        }
    )
