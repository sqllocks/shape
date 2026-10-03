"""Helpers of the consumer-contract tests: a producer profile (customers, orders and an audit
table) and consumer contract documents."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

import shape

PRODUCER_OWNERS = {"orders.amount": "finance-data@example.com", "status": "ops@example.com"}


def make_profile(
    path: Path,
    *,
    drop: tuple[str, str] | tuple[tuple[str, str], ...] | None = None,
    drop_table: str | None = None,
    note_null: float = 0.0,
    amount_text: bool = False,
    name: str = "orders",
) -> Path:
    rng = np.random.default_rng(3)
    n, m = 200, 800
    tables: dict[str, dict[str, Any]] = {
        "customer": {
            "customer_id": np.arange(1, n + 1, dtype="int64"),
            "segment": rng.choice(["retail", "wholesale"], n, p=[0.7, 0.3]),
            "age": np.clip(rng.normal(40, 12, n), 18, 90).astype("int64"),
        },
        "orders": {
            "order_id": np.arange(1, m + 1, dtype="int64"),
            "customer_id": rng.integers(1, n + 1, m).astype("int64"),
            "amount": rng.lognormal(4.0, 0.5, m).round(2),
            "status": rng.choice(["new", "paid", "shipped"], m),
            "note": np.array(
                [None if i < note_null * m else f"n{i % 31}" for i in range(m)], dtype=object
            ),
        },
        "audit": {"audit_id": np.arange(1, 31, dtype="int64"), "kind": ["x"] * 30},
    }
    if amount_text:
        tables["orders"]["amount"] = np.array([f"USD {x}" for x in tables["orders"]["amount"]])
    if drop is not None:
        pairs = [drop] if isinstance(drop[0], str) else list(drop)
        for table, column in pairs:  # type: ignore[misc]
            del tables[table][column]
    if drop_table is not None:
        del tables[drop_table]
    folder = path.parent / (path.stem + "_data")
    folder.mkdir(parents=True, exist_ok=True)
    sources = {}
    for tname, cols in tables.items():
        pq.write_table(pa.table(cols), folder / f"{tname}.parquet")
        sources[tname] = str(folder / f"{tname}.parquet")
    shape.save(shape.profile(sources, name=name), str(path))
    return path


def contract(consumer: str, body: dict[str, Any], **over: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "format": "shape-consumer-contract",
        "version": 1,
        "consumer": consumer,
        "owner": f"{consumer}@example.com",
        "source": "orders",
        "since": "2026-10-03",
        "requires": body,
    }
    doc.update(over)
    return doc


FINANCE = {
    "tables": {
        "orders": {
            "required_columns": ["order_id", "amount"],
            "columns": {"amount": {"dtype": "float", "max_null_rate": 0.0}},
        }
    }
}
MARKETING = {
    "tables": {
        "customer": {
            "columns": {
                "segment": {"allowed_values": ["retail", "wholesale"]},
                "age": {"dtype": "integer", "min": 18},
            }
        }
    }
}
