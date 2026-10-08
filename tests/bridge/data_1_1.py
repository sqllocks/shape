"""Datasets of the bridge 1.1 tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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


def retail_design() -> dict[str, Any]:
    """The retail design input of the design tests: 3NF, star and snowflake all differ."""

    def attr(name: str, type_: str = "string", **kw: Any) -> dict[str, Any]:
        return {"name": name, "type": type_, **kw}

    return {
        "format": "shape-design",
        "version": 1,
        "name": "retail",
        "entities": [
            {
                "name": "Customer",
                "attributes": [
                    attr("customer_id", "integer", nullable=False),
                    attr("name", max_length=80),
                    attr("city", max_length=60),
                    attr("region", max_length=60),
                    attr("country", max_length=60),
                ],
                "keys": [["customer_id"]],
                "dependencies": [
                    {"determinant": ["city"], "dependent": ["region"]},
                    {"determinant": ["region"], "dependent": ["country"]},
                ],
                "history": {"default": 1, "attributes": {"city": 2, "name": 3}},
            },
            {
                "name": "Product",
                "attributes": [
                    attr("product_id", "integer", nullable=False),
                    attr("sku", max_length=20),
                    attr("category", max_length=40),
                    attr("department", max_length=40),
                ],
                "keys": [["product_id"]],
                "dependencies": [{"determinant": ["category"], "dependent": ["department"]}],
            },
            {
                "name": "Promotion",
                "attributes": [
                    attr("promotion_id", "integer", nullable=False),
                    attr("label", max_length=40),
                ],
                "keys": [["promotion_id"]],
            },
            {
                "name": "OrderLine",
                "attributes": [
                    attr("order_id", "integer", nullable=False),
                    attr("line_no", "integer", nullable=False),
                    attr("customer_id", "integer", nullable=False, references="Customer"),
                    attr("product_id", "integer", nullable=False, references="Product"),
                    attr("order_date", "date", nullable=False),
                    attr("ship_date", "date"),
                    attr("quantity", "integer", nullable=False),
                    attr("amount", "decimal", precision=18, scale=2, nullable=False),
                    attr("discount_pct", "decimal", precision=5, scale=2),
                    attr("status", max_length=12),
                    attr("priority", max_length=8),
                    attr("is_gift", "boolean"),
                ],
                "keys": [["order_id", "line_no"]],
            },
        ],
        "hierarchies": [
            {"name": "geography", "entity": "Customer", "levels": ["city", "region", "country"]},
            {"name": "merchandise", "entity": "Product", "levels": ["category", "department"]},
        ],
        "facts": [
            {
                "name": "sales",
                "source": "OrderLine",
                "grain": ["order_id", "line_no"],
                "measures": [
                    {"name": "quantity", "attribute": "quantity", "additivity": "additive"},
                    {"name": "amount", "attribute": "amount", "additivity": "additive"},
                    {
                        "name": "discount_pct",
                        "attribute": "discount_pct",
                        "additivity": "non_additive",
                    },
                ],
                "dimensions": [
                    {"entity": "Customer", "via": "customer_id"},
                    {"entity": "Product", "via": "product_id"},
                ],
                "dates": ["order_date", "ship_date"],
                "degenerate": ["order_id", "line_no"],
                "junk": ["status", "priority", "is_gift"],
                "many_to_many": ["Promotion"],
            }
        ],
    }


def tiny_design() -> dict[str, Any]:
    """One entity with a key and a functional dependency, and no fact (3NF only)."""
    return {
        "format": "shape-design",
        "version": 1,
        "name": "tiny",
        "entities": [
            {
                "name": "City",
                "attributes": [
                    {"name": "city_id", "type": "integer", "nullable": False},
                    {"name": "city", "type": "string"},
                    {"name": "country", "type": "string"},
                ],
                "keys": [["city_id"]],
                "dependencies": [{"determinant": ["city"], "dependent": ["country"]}],
            }
        ],
    }


def keyless_design() -> dict[str, Any]:
    """Lint warnings only (K001: an entity declares no key)."""
    doc = tiny_design()
    doc["name"] = "keyless"
    doc["entities"][0]["keys"] = []
    return doc


def failing_design() -> dict[str, Any]:
    """Lint errors: a fact without a declared grain (D001)."""
    doc = retail_design()
    doc["name"] = "failing"
    doc["facts"][0]["grain"] = []
    return doc


DESIGNS = {
    "retail": retail_design,
    "tiny": tiny_design,
    "keyless": keyless_design,
    "failing": failing_design,
}


def write_design(folder: Path, name: str) -> Path:
    path = folder / f"{name}.design.json"
    path.write_text(json.dumps(DESIGNS[name](), indent=2), encoding="utf-8")
    return path
