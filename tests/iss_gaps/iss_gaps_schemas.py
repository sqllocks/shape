"""A daily-orders schema with no post-pass: customers with a stable pattern key, orders pointing at
them (the shape of the incremental-batch issue)."""

from __future__ import annotations

from typing import Any

from shape.generation.schema import GenSchema


def _col(name: str, strategy: str, type_: str = "integer", **gen: Any) -> dict[str, Any]:
    return {"name": name, "type": type_, "generator": {"strategy": strategy, **gen}}


def daily_schema(seed: int = 11) -> dict[str, Any]:
    tables = {
        "customer": {
            "name": "customer",
            "primary_key": ["customer_id"],
            "columns": {
                "customer_id": _col("customer_id", "pattern", "string", format="C{seq:9}"),
                "segment": _col(
                    "segment", "weighted_enum", "string", values={"a": 0.5, "b": 0.3, "c": 0.2}
                ),
                "notes": _col(
                    "notes", "weighted_enum", "string", values={"call back": 0.7, "vip": 0.3}
                ),
            },
        },
        "order": {
            "name": "order",
            "primary_key": ["order_id"],
            "columns": {
                "order_id": _col("order_id", "sequence"),
                "customer_id": _col(
                    "customer_id", "foreign_key", "string", ref="customer.customer_id"
                ),
                "amount": _col("amount", "distribution", "float", low=5.0, high=500.0),
                "status": _col(
                    "status",
                    "weighted_enum",
                    "string",
                    values={"open": 0.5, "shipped": 0.3, "closed": 0.2},
                ),
                "order_date": _col(
                    "order_date", "temporal", "timestamp", start="2026-01-01", end="2026-01-31"
                ),
            },
        },
    }
    doc = {
        "schema_version": 1,
        "model": {"name": "daily", "seed": seed},
        "tables": tables,
        "relationships": [
            {
                "name": "o_c",
                "parent": "customer",
                "child": "order",
                "parent_columns": ["customer_id"],
                "child_columns": ["customer_id"],
            }
        ],
        "generation": {"scale": "small", "scales": {"small": {"customer": 10, "order": 20}}},
    }
    return doc


def daily_gen_schema(seed: int = 11) -> GenSchema:
    return GenSchema.from_dict(daily_schema(seed))
