"""Datasets of the bridge 1.1 tests."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.csv as pacsv

EMAILS = [f"person{i}@example.com" for i in range(1, 51)]


def shop_tables() -> dict[str, pa.Table]:
    """customers (50) <- orders (200); the customers' emails are a classified column."""
    customers = pa.table(
        {
            "customer_id": list(range(1, 51)),
            "email": EMAILS,
            "full_name": [f"Person Number{i}" for i in range(1, 51)],
        }
    )
    orders = pa.table(
        {
            "order_id": list(range(1, 201)),
            "customer_id": [(i % 50) + 1 for i in range(200)],
            "status_code": [(i % 5) + 1 for i in range(200)],
            "amount": [round(10 + i * 0.5, 2) for i in range(200)],
        }
    )
    return {"customers": customers, "orders": orders}


def mail_tables() -> dict[str, pa.Table]:
    """users (50) <- messages (200) by the email address: the relationship's range endpoints are
    the first and last email, raw values of a classified column."""
    users = pa.table({"email": EMAILS, "display": [f"User {i}" for i in range(50)]})
    messages = pa.table(
        {
            "message_id": list(range(1, 201)),
            "email": [EMAILS[i % 50] for i in range(200)],
            "body": [f"hello {i % 7}" for i in range(200)],
        }
    )
    return {"users": users, "messages": messages}


def write_dataset(folder: Path, tables: dict[str, pa.Table]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for name, table in tables.items():
        pacsv.write_csv(table, folder / f"{name}.csv")
    return folder
