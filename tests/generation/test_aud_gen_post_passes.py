"""AUD-gen: the post-passes keep each other's results (#169)."""

from __future__ import annotations

from collections import defaultdict

import numpy as np
import pyarrow.compute as pc  # type: ignore[import-untyped]
from aud_gen_fixtures import build, col

from shape.generation.engine import Engine


def _orders(**kw):
    tables = {
        "order": (
            ["order_id"],
            {
                "order_id": col("sequence"),
                "total": col(
                    "computed",
                    "float",
                    rule="sum_children",
                    child_table="order_line",
                    child_column="amount",
                ),
            },
        ),
        "order_line": (
            ["line_id"],
            {
                "line_id": col("sequence"),
                "order_id": col("foreign_key", ref="order.order_id"),
                "amount": col("distribution", "float", low=1.0, high=50.0),
                "cost": col("distribution", "float", low=1.0, high=50.0),
            },
        ),
    }
    return build(
        tables,
        {"order": 50, "order_line": 200},
        rels=(("order", "order_line", "order_id", "order_id"),),
        **kw,
    )


def test_the_copula_does_not_break_computed_sums_or_repaired_rules():
    # 169: the copula ran last and reordered order_line.amount and cost on their own, so 49 of
    # 50 totals no longer matched their lines, 13 rows broke cost < amount, and
    # remaining_violations (computed before the copula) was empty.
    s = _orders(
        corr={"order_line": [["amount", "cost", 0.9]]},
        rules=(
            {
                "name": "cost_lt_amount",
                "type": "cross_column",
                "rule": "cost < amount",
                "table": "order_line",
            },
        ),
    )
    engine = Engine(s)
    res = engine.generate()
    order, line = res.tables["order"], res.tables["order_line"]
    sums: dict[int, float] = defaultdict(float)
    for oid, amount in zip(line["order_id"].to_pylist(), line["amount"].to_pylist(), strict=True):
        sums[oid] += amount
    for oid, total in zip(order["order_id"].to_pylist(), order["total"].to_pylist(), strict=True):
        assert abs(round(sums[oid], 2) - total) <= 0.01
    assert res.remaining_violations == engine.validate(res.tables)
    assert pc.sum(pc.greater_equal(line["cost"], line["amount"])).as_py() == sum(
        v.violation_count for v in res.remaining_violations
    )


def test_the_copula_still_correlates_when_nothing_else_reads_the_table():
    s = _orders(corr={"order_line": [["amount", "cost", 0.9]]})
    line = Engine(s).generate().tables["order_line"]
    a = line["amount"].to_numpy()
    b = line["cost"].to_numpy()
    assert np.corrcoef(a, b)[0, 1] > 0.7
