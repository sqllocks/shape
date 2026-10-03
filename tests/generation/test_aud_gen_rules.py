"""AUD-gen: business-rule repair (#170, #192)."""

from __future__ import annotations

from aud_gen_fixtures import build, col

from shape.generation.engine import Engine


def _customers_and_orders(rule: str):
    tables = {
        "customer": (
            ["customer_id"],
            {
                "customer_id": col("sequence"),
                "churned": col("temporal", "timestamp", start="2023-01-01", end="2023-12-31"),
            },
        ),
        "order": (
            ["order_id"],
            {
                "order_id": col("sequence"),
                "customer_id": col("foreign_key", ref="customer.customer_id"),
                "placed": col("temporal", "timestamp", start="2023-01-01", end="2024-12-31"),
            },
        ),
    }
    return build(
        tables,
        {"customer": 20, "order": 100},
        rels=(("customer", "order", "customer_id", "customer_id"),),
        rules=(
            {"name": "before_churn", "type": "cross_table", "rule": rule, "via": "customer_id"},
        ),
    )


def test_a_temporal_cross_table_rule_with_le_is_repaired():
    # 170: the <= branch multiplied timestamps by a float: UFuncTypeError.
    res = Engine(_customers_and_orders("order.placed <= customer.churned")).generate()
    assert res.remaining_violations == []
    churned = dict(
        zip(
            res.tables["customer"]["customer_id"].to_pylist(),
            res.tables["customer"]["churned"].to_pylist(),
            strict=True,
        )
    )
    order = res.tables["order"]
    for cid, placed in zip(
        order["customer_id"].to_pylist(), order["placed"].to_pylist(), strict=True
    ):
        assert placed <= churned[cid]
