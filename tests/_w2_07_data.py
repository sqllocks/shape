"""Deterministic fixtures for the W2-07 tests (sampling record, type inference)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]


def orders_table(n: int = 600, seed: int = 5) -> pa.Table:
    """Orders with a numeric, a text, a date, a boolean and a nullable column."""
    rng = np.random.RandomState(seed)
    amount = np.round(rng.gamma(2.0, 30.0, n), 2)
    status = rng.choice(["new", "paid", "shipped", "returned"], n, p=[0.1, 0.5, 0.3, 0.1])
    day = (np.datetime64("2026-01-01") + rng.randint(0, 200, n)).astype("datetime64[D]")
    flag = rng.rand(n) < 0.3
    note = [None if rng.rand() < 0.2 else f"n{int(v)}" for v in rng.randint(0, 40, n)]
    return pa.table(
        {
            "order_id": pa.array(np.arange(1, n + 1, dtype=np.int64)),
            "amount": pa.array(amount),
            "status": pa.array(status.tolist()),
            "ordered_on": pa.array(day, pa.date32()),
            "gift": pa.array(flag),
            "note": pa.array(note, pa.string()),
        }
    )


def write_orders_csv(path: Path, n: int = 600, seed: int = 5) -> Path:
    import pyarrow.csv as pacsv  # type: ignore[import-untyped]

    pacsv.write_csv(orders_table(n, seed), path)
    return path


def write_orders_parquet(path: Path, n: int = 600, seed: int = 5) -> Path:
    pq.write_table(orders_table(n, seed), path)
    return path


def shop_tables(customers: int = 300, orders: int = 1500, seed: int = 11) -> dict[str, pa.Table]:
    """A parent and a child table linked by ``customer_id``."""
    rng = np.random.RandomState(seed)
    cust = pa.table(
        {
            "customer_id": pa.array(np.arange(1, customers + 1, dtype=np.int64)),
            "region": pa.array(rng.choice(["n", "s", "e", "w"], customers).tolist()),
        }
    )
    ords = pa.table(
        {
            "order_id": pa.array(np.arange(1, orders + 1, dtype=np.int64)),
            "customer_id": pa.array(rng.randint(1, customers + 1, orders).astype(np.int64)),
            "total": pa.array(np.round(rng.gamma(2.0, 40.0, orders), 2)),
        }
    )
    return {"customer": cust, "orders": ords}


def write_shop_dir(folder: Path, **kwargs: Any) -> Path:
    import pyarrow.csv as pacsv  # type: ignore[import-untyped]

    folder.mkdir(parents=True, exist_ok=True)
    for name, t in shop_tables(**kwargs).items():
        pacsv.write_csv(t, folder / f"{name}.csv")
    return folder
