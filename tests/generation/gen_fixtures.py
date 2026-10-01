"""A small schema for the generation tests: customer <- order <- order_line."""

from __future__ import annotations

from typing import Any

from shape.generation.schema import GenSchema


def col(strategy: str, type_: str = "integer", **gen: Any) -> dict[str, Any]:
    nullable = gen.pop("nullable", False)
    null_rate = gen.pop("null_rate", 0.0)
    return {
        "name": "",
        "type": type_,
        "generator": {"strategy": strategy, **gen},
        "nullable": nullable,
        "null_rate": null_rate,
    }


def schema(rows: dict[str, int] | None = None, **model: Any) -> GenSchema:
    """customer <- order <- order_line, with a computed total on order."""
    rows = rows or {"customer": 40, "order": 120, "order_line": 300}
    tables = {
        "customer": {
            "primary_key": ["customer_id"],
            "columns": {
                "name": col(
                    "weighted_enum",
                    "string",
                    values=["a", "b", "c", "d"],
                    nullable=True,
                    null_rate=0.2,
                ),
                "customer_id": col("sequence", start=1000),
                "score": col("distribution", "float", low=0.0, high=100.0),
            },
        },
        "order": {
            "primary_key": ["order_id"],
            "columns": {
                "total": col(
                    "computed",
                    "float",
                    rule="sum_children",
                    child_table="order_line",
                    child_column="amount",
                ),
                "double_score": col("derived", "float", source="score"),
                "score": col("distribution", "float", low=0.0, high=10.0),
                "customer_id": col("foreign_key", ref="customer.customer_id"),
                "order_id": col("sequence"),
            },
        },
        "order_line": {
            "primary_key": ["line_id"],
            "columns": {
                "amount": col("distribution", "float", low=1.0, high=50.0),
                "order_id": col("foreign_key", ref="order.order_id"),
                "line_id": col("sequence"),
            },
        },
    }
    for t, td in tables.items():
        td["name"] = t
        for c, cd in td["columns"].items():
            cd["name"] = c
    doc = {
        "schema_version": 1,
        "model": {"name": "t", "seed": 5, **model},
        "tables": tables,
        "relationships": [
            {
                "name": "o_c",
                "parent": "customer",
                "child": "order",
                "parent_columns": ["customer_id"],
                "child_columns": ["customer_id"],
            },
            {
                "name": "l_o",
                "parent": "order",
                "child": "order_line",
                "parent_columns": ["order_id"],
                "child_columns": ["order_id"],
            },
        ],
        "generation": {"scale": "small", "scales": {"small": rows}},
    }
    return GenSchema.from_dict(doc)
