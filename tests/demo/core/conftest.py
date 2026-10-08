"""Shared fixtures for the demo core tests."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest


def make_orders(n: int = 400, seed: int = 7) -> pa.Table:
    """A small, deterministic table with every column family the profiler distinguishes."""
    rng = np.random.default_rng(seed)
    status = rng.choice(["placed", "shipped", "returned"], size=n, p=[0.5, 0.4, 0.1])
    email = np.array([f"user{i}@example.com" for i in range(n)], dtype=object)
    email_null = rng.random(n) < 0.05
    amount = np.round(rng.lognormal(3.0, 0.6, size=n), 2)
    order_date = (np.datetime64("2026-01-01") + rng.integers(0, 90, size=n)).astype("datetime64[s]")
    return pa.table(
        {
            "order_id": pa.array(np.arange(1, n + 1, dtype=np.int64)),
            "customer_id": pa.array(rng.integers(1, 60, size=n, dtype=np.int64)),
            "email": pa.array(
                [None if m else str(v) for v, m in zip(email, email_null, strict=True)]
            ),
            "status": pa.array(status.tolist()),
            "amount": pa.array(amount),
            "order_date": pa.array(order_date),
        }
    )


@pytest.fixture()
def orders() -> pa.Table:
    return make_orders()


@pytest.fixture()
def customers() -> pa.Table:
    n = 60
    return pa.table(
        {
            "customer_id": pa.array(np.arange(1, n + 1, dtype=np.int64)),
            "name": pa.array([f"name{i}" for i in range(n)]),
        }
    )
