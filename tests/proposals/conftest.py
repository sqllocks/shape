"""Shared data for the W1-02 tests: a small shop with real foreign keys, a decoy and PII."""

from __future__ import annotations

import pyarrow as pa
import pytest

import shape

NOW = "2026-10-03T12:00:00Z"
LATER = "2026-10-04T08:30:00Z"


def shop_tables(orphans: int = 0) -> dict[str, pa.Table]:
    """customers (50) <- orders (200) -> products (20); ``orphans`` order rows point nowhere."""
    customers = pa.table(
        {
            "customer_id": list(range(1, 51)),
            "email": [f"person{i}@example.com" for i in range(1, 51)],
            "full_name": [f"Person Number{i}" for i in range(1, 51)],
            "notes": [f"note {i % 3}" for i in range(1, 51)],
        }
    )
    products = pa.table(
        {"product_id": list(range(100, 120)), "label": [f"item-{i}" for i in range(20)]}
    )
    cust = [(i % 50) + 1 for i in range(200)]
    for i in range(orphans):
        cust[i] = 9_000 + i
    orders = pa.table(
        {
            "order_id": list(range(1, 201)),
            "customer_id": cust,
            "product_id": [100 + (i % 20) for i in range(200)],
            # a decoy: small integers that happen to sit inside the key ranges
            "status_code": [(i % 5) + 1 for i in range(200)],
            # same values as a key but text: a type mismatch
            "customer_ref": [str((i % 50) + 1) for i in range(200)],
            "amount": [round(10 + i * 0.5, 2) for i in range(200)],
        }
    )
    return {"customers": customers, "orders": orders, "products": products}


@pytest.fixture
def tables() -> dict[str, pa.Table]:
    return shop_tables()


@pytest.fixture
def profile(tables):
    return shape.profile(tables)
