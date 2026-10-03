"""AUD-gen: business-rule repair (#170, #192)."""

from __future__ import annotations

import pytest
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


def _pairs(left, right, type_=None):
    import pyarrow as pa  # type: ignore[import-untyped]

    return pa.table({"a": pa.array(left, type_), "b": pa.array(right, type_)})


@pytest.mark.parametrize(
    ("op", "left", "right", "type_"),
    [
        ("<", [5.0, 5.0, 5.0], [-10.0, 0.0, 0.004], "float64"),
        (">", [-50.0, -5.0], [-10.0, 0.0], "float64"),
        ("<", [3] * 10, [1] * 10, "int64"),
        (">", [0] * 10, [1] * 10, "int64"),
    ],
)
def test_a_cross_column_repair_satisfies_its_rule_for_any_bound(op, left, right, type_):
    # 192: the repair scaled the bound by 0.3..0.95 (or 1.05..2), which only works for positive
    # bounds: a < b with b = -10, 0, 0.004 still broke the rule after the repair.
    import pyarrow as pa  # type: ignore[import-untyped]
    import pyarrow.compute as pc  # type: ignore[import-untyped]

    from shape.generation.rules import fix_rule
    from shape.generation.schema import BusinessRule

    rule = BusinessRule(name="r", type="cross_column", rule=f"a {op} b", table="t")
    fixed = fix_rule(rule, {"t": _pairs(left, right, pa.type_for_alias(type_))}, 7)["t"]
    test = pc.less if op == "<" else pc.greater
    assert all(test(fixed["a"], fixed["b"]).to_pylist()), fixed.to_pydict()


def test_a_cross_table_le_repair_satisfies_its_rule_for_a_negative_bound():
    # 192: c.v <= p.lim with lim = -10 gave v = -5.46, still above the bound.
    import pyarrow as pa  # type: ignore[import-untyped]

    from shape.generation.rules import fix_rule
    from shape.generation.schema import BusinessRule

    rule = BusinessRule(name="r", type="cross_table", rule="c.v <= p.lim", via="pid")
    tables = {
        "p": pa.table({"pid": [1, 2], "lim": [-10.0, 0.0]}),
        "c": pa.table({"pid": [1, 2, 1], "v": [5.0, 3.0, -20.0]}),
    }
    fixed = fix_rule(rule, tables, 7)["c"]
    lim = {1: -10.0, 2: 0.0}
    for pid, v in zip(fixed["pid"].to_pylist(), fixed["v"].to_pylist(), strict=True):
        assert v <= lim[pid]


def test_a_between_constraint_is_checked():
    # 327: smart inference writes "rating BETWEEN 1 AND 5", which the rules engine skipped.
    import pyarrow as pa  # type: ignore[import-untyped]

    from shape.generation.rules import validate_rules
    from shape.generation.schema import BusinessRule

    schema = _customers_and_orders("order.placed <= customer.churned")
    schema.business_rules = [
        BusinessRule(name="r", type="constraint", rule="rating BETWEEN 1 AND 5", table="t")
    ]
    tables = {"t": pa.table({"rating": [0.5, 1.0, 3.0, 5.0, 6.0, None]})}
    (violation,) = validate_rules(tables, schema)
    assert (violation.rule_name, violation.violation_count) == ("r", 2)
    assert not [i for i in schema.validate() if "never checked" in i.message]


def test_inferred_date_order_rules_name_their_tables():
    # 327: BR-04 wrote the cross_table rule "order_date >= order_date" without table prefixes,
    # so it was never checked or repaired.
    from shape.generation.ddl import from_ddl

    schema, _ = from_ddl(
        "CREATE TABLE orders (id INT PRIMARY KEY, order_date DATE NOT NULL, total DECIMAL(10,2));"
        "CREATE TABLE order_items (id INT PRIMARY KEY, order_id INT REFERENCES orders(id), "
        "order_date DATE NOT NULL, quantity INT, unit_price DECIMAL(10,2))"
    )
    rules = {r.name: r.rule for r in schema.business_rules if r.type == "cross_table"}
    assert rules == {"order_items_after_orders": "order_items.order_date >= orders.order_date"}
    res = Engine(schema).generate()
    assert not [v for v in res.remaining_violations if v.rule_name == "order_items_after_orders"]
