"""Environments for the parity tests: a small customers/orders dataset written as parquet, with
the knobs the tests turn (a dropped column, a changed type, an orphaned foreign key, null rates,
sizes)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest


def build_env(
    folder: Path,
    *,
    customers: int = 400,
    orders: int = 1600,
    seed: int = 1,
    note_null: float = 0.0,
    drop: tuple[str, str] | None = None,
    amount_as_text: bool = False,
    orphan_fk: bool = False,
    extra_table: bool = False,
    skip_table: str | None = None,
    extra_column: bool = False,
    amount_scale: float = 1.0,
    status_p: tuple[float, float, float] = (0.2, 0.5, 0.3),
) -> Path:
    rng = np.random.default_rng(seed)
    folder.mkdir(parents=True, exist_ok=True)
    cust: dict[str, Any] = {
        "customer_id": np.arange(1, customers + 1, dtype="int64"),
        "segment": rng.choice(["retail", "wholesale", "online"], customers, p=[0.6, 0.3, 0.1]),
        "age": np.clip(rng.normal(40, 12, customers), 18, 90).astype("int64"),
        "balance": rng.lognormal(5.0, 0.8, customers).round(2),
    }
    fk = rng.integers(1, customers + 1, orders).astype("int64")
    if orphan_fk:
        fk = fk + 1_000_000  # no customer has these ids
    amount = (rng.lognormal(4.0, 0.6, orders) * amount_scale).round(2)
    note = np.array([f"n{i % 97}" for i in range(orders)], dtype=object)
    if note_null:
        note[: round(orders * note_null)] = None  # exact, so a boundary can be tested
    order: dict[str, Any] = {
        "order_id": np.arange(1, orders + 1, dtype="int64"),
        "customer_id": fk,
        "amount": np.array([f"USD {x}" for x in amount]) if amount_as_text else amount,
        "status": rng.choice(["new", "paid", "shipped"], orders, p=list(status_p)),
        "note": note,
    }
    if extra_column:
        order["channel"] = rng.choice(["web", "shop"], orders)
    tables = {"customer": cust, "orders": order}
    if extra_table:
        tables["audit"] = {"audit_id": np.arange(1, 51, dtype="int64"), "kind": ["x"] * 50}
    if drop is not None:
        del tables[drop[0]][drop[1]]
    for name, cols in tables.items():
        if name == skip_table:
            continue
        pq.write_table(pa.table(cols), folder / f"{name}.parquet")
    return folder


@pytest.fixture
def env(tmp_path: Path):
    """``env("prod", **knobs)`` writes and returns a dataset folder under the test's tmp path."""

    def make(name: str, **knobs: Any) -> Path:
        return build_env(tmp_path / name, **knobs)

    return make
