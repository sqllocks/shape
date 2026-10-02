"""Small schemas for the scale tests."""

from __future__ import annotations

from typing import Any

from shape.generation.schema import GenSchema


def _col(name: str, strategy: str, type_: str = "integer", **gen: Any) -> dict[str, Any]:
    return {
        "name": name,
        "type": type_,
        "generator": {"strategy": strategy, **gen},
        "nullable": False,
        "null_rate": 0.0,
    }


def plain_doc(rows: dict[str, int] | None = None, seed: int = 5) -> dict[str, Any]:
    """customer <- order <- order_line: no post-pass, so every table streams chunk by chunk."""
    rows = rows or {"customer": 40, "order": 1200, "order_line": 3100}
    tables = {
        "customer": {
            "name": "customer",
            "primary_key": ["customer_id"],
            "columns": {
                "customer_id": _col("customer_id", "sequence", start=1000),
                "name": _col(
                    "name", "weighted_enum", "string", values={"a": 4, "b": 3, "c": 2, "d": 1}
                ),
                "score": _col("score", "distribution", "float", low=0.0, high=100.0),
            },
        },
        "order": {
            "name": "order",
            "primary_key": ["order_id"],
            "columns": {
                "order_id": _col("order_id", "sequence"),
                "customer_id": _col("customer_id", "foreign_key", ref="customer.customer_id"),
                "score": _col("score", "distribution", "float", low=0.0, high=10.0),
            },
        },
        "order_line": {
            "name": "order_line",
            "primary_key": ["line_id"],
            "columns": {
                "line_id": _col("line_id", "sequence"),
                "order_id": _col("order_id", "foreign_key", ref="order.order_id"),
                "amount": _col("amount", "distribution", "float", low=1.0, high=50.0),
            },
        },
    }
    return {
        "schema_version": 1,
        "model": {"name": "scale_t", "seed": seed},
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


def plain_schema(rows: dict[str, int] | None = None, seed: int = 5) -> GenSchema:
    return GenSchema.from_dict(plain_doc(rows, seed))


def computed_doc(rows: dict[str, int] | None = None) -> dict[str, Any]:
    """The same, with a ``computed`` total on order: a post-pass, so order cannot stream."""
    doc = plain_doc(rows)
    doc["tables"]["order"]["columns"]["total"] = _col(
        "total",
        "computed",
        "float",
        rule="sum_children",
        child_table="order_line",
        child_column="amount",
    )
    return doc


def computed_schema(rows: dict[str, int] | None = None) -> GenSchema:
    return GenSchema.from_dict(computed_doc(rows))
