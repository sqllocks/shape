"""Shared design inputs for the W5-02 tests."""

from __future__ import annotations

import copy
from typing import Any

import pytest


def retail_doc() -> dict[str, Any]:
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


@pytest.fixture
def doc() -> dict[str, Any]:
    return copy.deepcopy(retail_doc())
