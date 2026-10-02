"""Business-rule repairs applied to each chunk as it is made (``plan_streamed_rules``) give exactly
what the plain order gives, which repairs whole tables after generation; and a comparison the
repair never changes (``high >= low`` on one table) does not hold its table back."""

from __future__ import annotations

import pyarrow as pa
import pytest
from engine_fixtures import STRATEGIES
from gen_fixtures import col, schema

from shape.generation.early_rules import plan_streamed_rules
from shape.generation.engine import Engine
from shape.generation.rules import can_repair, fix_rule, repair_target, repaired_tables
from shape.generation.schema import BusinessRule, Column

ROWS = {"customer": 40, "order": 1500, "order_line": 6000}


def _schema(*rules: BusinessRule, rows=None):
    """customer <- order <- order_line; order_line gets two plain float columns for the rules."""
    s = schema(rows or ROWS)
    lines = s.tables["order_line"].columns
    lines["ship_score"] = _column("ship_score", col("distribution", "float", low=0.0, high=100.0))
    lines["pack_score"] = _column("pack_score", col("distribution", "float", low=0.0, high=100.0))
    s.business_rules = list(rules)
    return s


def _column(name: str, spec: dict) -> Column:
    return Column(
        name=name,
        type=spec["type"],
        generator=spec["generator"],
        nullable=spec["nullable"],
        null_rate=spec["null_rate"],
    )


# repairs ship_score of an order line from the order's score: a cross_table rule on a table with no
# computed column whose parent column nothing else rewrites
FROM_ORDER = BusinessRule(
    "ship_after_order", "cross_table", "order_line.ship_score >= order.score", via="order_id"
)
# the operator that draws from a row-addressed stream
FROM_ORDER_LE = BusinessRule(
    "ship_before_order", "cross_table", "order_line.ship_score <= order.score", via="order_id"
)
WITHIN_LINE = BusinessRule(
    "ship_below_pack", "cross_column", "ship_score < pack_score", "order_line"
)
WITHIN_LINE_GT = BusinessRule(
    "pack_above_ship", "cross_column", "pack_score > ship_score", "order_line"
)
# the compute phase sums order_line.amount: its repair has to wait for that phase
SUMMED = BusinessRule("amount_below_order", "cross_column", "amount < order_id", "order_line")
# order has a computed column, so none of its rules can be streamed
ON_ORDER = BusinessRule(
    "order_after_customer", "cross_table", "order.score >= customer.score", via="customer_id"
)
NOOP_SAME_TABLE = BusinessRule(
    "ship_at_least_pack", "cross_column", "ship_score >= pack_score", "order_line"
)


def _engine(s, *, stream: bool, chunk: int = 700) -> Engine:
    engine = Engine(s, strategies=STRATEGIES, chunk_rows=chunk)
    engine._stream_rules = stream
    return engine


def _plan(s):
    engine = Engine(s, strategies=STRATEGIES)
    return plan_streamed_rules(s, engine.levels, engine._unstreamable_tables())


# ---- which comparisons repair anything -------------------------------------------------------


@pytest.mark.parametrize(
    "rule,expected",
    [
        (WITHIN_LINE, True),
        (WITHIN_LINE_GT, True),
        (NOOP_SAME_TABLE, False),  # >= on one table is only validated
        (BusinessRule("x", "cross_column", "a <= b", "t"), False),
        (BusinessRule("x", "cross_column", "a == b", "t"), False),
        (FROM_ORDER, True),
        (FROM_ORDER_LE, True),
        (BusinessRule("x", "cross_table", "t.a < u.b", via="k"), False),
        (BusinessRule("x", "cross_table", "t.a == u.b", via="k"), False),
        (BusinessRule("x", "constraint", "a > 0", "t"), False),
    ],
)
def test_can_repair(rule, expected) -> None:
    assert can_repair(rule) is expected
    assert (repair_target(rule) is not None) is expected


def test_a_rule_that_cannot_repair_does_not_touch_its_table() -> None:
    s = _schema(NOOP_SAME_TABLE)
    assert repaired_tables(s) == set()
    s = _schema(WITHIN_LINE)
    assert repaired_tables(s) == {"order_line"}


@pytest.mark.parametrize("rule", [NOOP_SAME_TABLE, WITHIN_LINE, FROM_ORDER, FROM_ORDER_LE])
def test_fix_rule_changes_rows_exactly_when_it_can_repair(rule) -> None:
    before = _engine(_schema(), stream=False).generate().tables
    after = fix_rule(rule, dict(before), 5)
    changed = any(not after[name].equals(before[name]) for name in before)
    assert changed is can_repair(rule)


# ---- the plan --------------------------------------------------------------------------------


def test_a_plain_rule_on_a_table_without_computed_columns_is_planned() -> None:
    plan = _plan(_schema(FROM_ORDER))
    assert {t: [i for i, _ in rules] for t, rules in plan.by_table.items()} == {"order_line": [0]}
    assert plan.indices == {0}
    assert plan


def test_rules_that_the_compute_phase_or_a_computed_table_forbid_are_not_planned() -> None:
    assert not _plan(_schema(SUMMED))  # the compute phase sums the column it rewrites
    assert not _plan(_schema(ON_ORDER))  # order has a computed column


def test_a_table_is_planned_with_all_its_rules_or_none() -> None:
    plan = _plan(_schema(FROM_ORDER, WITHIN_LINE))
    assert [i for i, _ in plan.by_table["order_line"]] == [0, 1]
    assert not _plan(_schema(FROM_ORDER, SUMMED))  # one rule of order_line cannot stream


def test_a_rule_that_reads_a_column_another_table_rewrites_streams_only_after_it() -> None:
    # order.score is rewritten by a rule on order: blocked (computed), so the dependent rule that
    # reads it cannot stream either
    assert not _plan(_schema(ON_ORDER, FROM_ORDER))
    assert not _plan(_schema(FROM_ORDER, ON_ORDER))


# ---- the result is the plain result -------------------------------------------------------------


RULE_SETS = {
    "cross_table >=": [FROM_ORDER],
    "cross_table <=": [FROM_ORDER_LE],
    "cross_column <": [WITHIN_LINE],
    "cross_column >": [WITHIN_LINE_GT],
    "two rules on one table": [FROM_ORDER, WITHIN_LINE],
    "streamed and not": [FROM_ORDER, SUMMED],
    "on a computed table": [ON_ORDER, FROM_ORDER],
    "nothing to repair": [NOOP_SAME_TABLE],
}


@pytest.mark.parametrize("threads", ["1", "4"])
@pytest.mark.parametrize("chunk", [700, 6000, 77])
@pytest.mark.parametrize("name", list(RULE_SETS))
def test_streamed_repairs_equal_the_plain_order(monkeypatch, name, chunk, threads) -> None:
    monkeypatch.setenv("SHAPE_THREADS", threads)
    s = _schema(*RULE_SETS[name])
    plain = _engine(s, stream=False, chunk=chunk).generate()
    fast = _engine(s, stream=True, chunk=chunk).generate()
    assert list(fast.tables) == list(plain.tables)
    for table, expected in plain.tables.items():
        assert fast.tables[table].equals(expected), table
    assert fast.remaining_violations == plain.remaining_violations


@pytest.mark.parametrize("name", ["cross_table >=", "cross_table <=", "cross_column <"])
def test_the_streamed_table_is_the_same_for_any_chunk_size(name) -> None:
    s = _schema(*RULE_SETS[name])
    small = _engine(s, stream=True, chunk=77).generate().tables["order_line"]
    large = _engine(s, stream=True, chunk=6000).generate().tables["order_line"]
    assert small.equals(large)


@pytest.mark.parametrize("threads", ["1", "4"])
def test_a_planned_table_is_delivered_repaired_while_generating(monkeypatch, threads) -> None:
    monkeypatch.setenv("SHAPE_THREADS", threads)
    s = _schema(
        FROM_ORDER, FROM_ORDER_LE.__class__("n", "constraint", "pack_score > -1", "order_line")
    )
    engine = _engine(s, stream=True)
    got: dict[str, list] = {}
    final: dict[str, bool] = {}

    def on_batch(name, batch) -> None:
        if batch is None:
            final[name] = True
            return
        got.setdefault(name, []).append(batch)

    result = engine.generate(on_batch=on_batch)
    assert final["order_line"]  # written as it was made: it is not held back for the post-passes
    streamed = pa.Table.from_batches(got["order_line"])
    assert streamed.equals(result.tables["order_line"])
    # and the rule really changed rows
    bare = _engine(_schema(), stream=True).generate().tables["order_line"]
    assert not streamed.equals(bare)
    assert result.remaining_violations == []


def test_a_table_whose_rules_cannot_repair_is_final_at_generation() -> None:
    s = _schema(NOOP_SAME_TABLE)
    engine = _engine(s, stream=True)
    final: set[str] = set()
    engine.generate(on_batch=lambda name, batch: final.add(name) if batch is None else None)
    assert "order_line" in final


def test_generating_twice_keeps_the_repaired_tables() -> None:
    s = _schema(FROM_ORDER)
    engine = _engine(s, stream=True)
    first = engine.generate().tables
    second = engine.generate().tables
    for name, table in first.items():
        assert second[name].equals(table)
    plain = _engine(s, stream=False).generate().tables
    for name, table in plain.items():
        assert first[name].equals(table)
