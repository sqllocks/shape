"""Two passes of ``Engine.generate`` that run while tables are still being made give exactly what
the plain order of the passes gives: ``sum_children`` / ``count_children`` columns accumulated chunk
by chunk (``StreamedAggregate``), and the leading business rules repaired on a helper thread
(``EarlyRules``)."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest
from engine_fixtures import STRATEGIES
from gen_fixtures import schema

from shape.generation import compute
from shape.generation.compute import StreamedAggregate, plan_streamed_aggregates
from shape.generation.early_rules import EarlyRules
from shape.generation.engine import Engine
from shape.generation.schema import BusinessRule

# ---- StreamedAggregate against the single pass ----------------------------------------------


def _parent(size: int, start: int = 1) -> pa.Table:
    return pa.table({"id": pa.array(np.arange(start, start + size, dtype=np.int64))})


def _child(rng: np.random.Generator, rows: int, size: int, start: int, kind: str) -> pa.Table:
    keys = rng.integers(start - 3, start + size + 3, rows)  # some keys outside the sequence
    key_nulls = rng.random(rows) < 0.05
    value_nulls = rng.random(rows) < 0.05
    if kind == "float":
        values = np.round(rng.random(rows) * 100, 2)
    else:
        values = rng.integers(-50, 50, rows)
    return pa.table(
        {
            "pid": pa.array(keys, mask=key_nulls, type=pa.int64()),
            "x": pa.array(values, mask=value_nulls),
        }
    )


@pytest.mark.parametrize("kind", ["float", "int"])
@pytest.mark.parametrize("rule", ["sum_children", "count_children"])
@pytest.mark.parametrize("start", [1, 1000])
def test_streamed_aggregate_is_the_single_pass(kind: str, rule: str, start: int) -> None:
    rng = np.random.default_rng(11)
    size, rows = 400, 5000
    parent, child = _parent(size, start), _child(rng, rows, size, start, kind)
    agg = StreamedAggregate("p", "id", "t", "c", "pid", "x", rule, start, size, rows)
    for offset in range(0, rows, 777):
        agg.feed(child.slice(offset, 777).to_batches()[0])
    got = agg.result(parent, child)
    want = compute._aggregate(parent, child, "id", "pid", "x", rule)
    assert got is not None
    assert got.type == want.type
    assert got.to_pylist() == want.to_pylist()  # bit for bit: floats are added in the same order


def test_streamed_aggregate_steps_aside() -> None:
    rng = np.random.default_rng(3)
    parent, child = _parent(50), _child(rng, 300, 50, 1, "float")
    batches = child.to_batches()
    agg = StreamedAggregate("p", "id", "t", "c", "pid", "x", "sum_children", 1, 50, 300)
    agg.feed(batches[0].slice(0, 100))
    assert agg.result(parent, child) is None  # not every row was seen
    agg = StreamedAggregate("p", "id", "t", "c", "pid", "x", "sum_children", 1, 50, 300)
    agg.feed(batches[0])
    assert agg.result(_parent(50, start=2), child) is None  # the parent's keys are not dense from 1
    assert agg.result(_parent(51), child) is None  # a different number of parent rows
    odd = pa.table({"pid": child["pid"].cast(pa.int32()), "x": child["x"]})
    agg = StreamedAggregate("p", "id", "t", "c", "pid", "x", "sum_children", 1, 50, 300)
    agg.feed(odd.to_batches()[0])
    assert agg.result(parent, child) is None  # a key column that is not int64


def test_only_the_native_kernel_case_is_planned() -> None:
    s = schema()
    (plan,) = plan_streamed_aggregates(s, {"order": 120, "order_line": 300})
    assert (plan.parent, plan.column, plan.child, plan.fk, plan.source) == (
        "order",
        "total",
        "order_line",
        "order_id",
        "amount",
    )
    s.tables["order"].columns["order_id"].generator["step"] = 2  # a gap in the keys: not dense
    assert plan_streamed_aggregates(s, {"order": 120, "order_line": 300}) == []
    s = schema()
    s.tables["order"].columns["total"].generator["rule"] = "avg_children"
    assert plan_streamed_aggregates(s, {"order": 120, "order_line": 300}) == []


# ---- the engine with the passes on and off --------------------------------------------------


def _engine(s, *, early: bool, stream: bool) -> Engine:
    engine = Engine(s, strategies=STRATEGIES, chunk_rows=2_000)
    engine._early_rules = early
    engine._stream_aggregates = stream
    return engine


def _with_rule(rule: BusinessRule):
    s = schema({"customer": 40, "order": 1500, "order_line": 4000})
    s.business_rules = [rule]
    return s


EARLY_OK = BusinessRule(
    "score_above_customer",
    "cross_table",
    "order.score >= customer.score",
    via="customer_id",
)
# Repairs the column the compute phase sums, so it has to wait for the compute phase's turn.
BLOCKED = BusinessRule("amount_below_order_id", "cross_column", "amount < order_id", "order_line")


@pytest.mark.parametrize("threads", ["1", "4"])
@pytest.mark.parametrize("rule", [None, EARLY_OK, BLOCKED], ids=["none", "early", "blocked"])
def test_passes_give_the_plain_result(monkeypatch, rule, threads: str) -> None:
    monkeypatch.setenv("SHAPE_THREADS", threads)
    s = (
        _with_rule(rule)
        if rule is not None
        else schema({"customer": 40, "order": 1500, "order_line": 4000})
    )
    plain = _engine(s, early=False, stream=False).generate()
    fast = _engine(s, early=True, stream=True).generate()
    assert list(fast.tables) == list(plain.tables)
    for name, table in plain.tables.items():
        assert fast.tables[name].equals(table), name
    assert fast.remaining_violations == plain.remaining_violations


def test_streamed_total_is_used_when_the_level_runs_on_threads(monkeypatch) -> None:
    monkeypatch.setenv("SHAPE_THREADS", "4")
    single_passes: list[str] = []
    original = compute._aggregate
    monkeypatch.setattr(
        compute, "_aggregate", lambda *a, **k: (single_passes.append(a[4]), original(*a, **k))[1]
    )
    s = schema({"customer": 40, "order": 1500, "order_line": 70_000})
    _engine(s, early=True, stream=True).generate()
    assert single_passes == []
    _engine(s, early=True, stream=False).generate()
    assert single_passes == ["amount"]


# ---- which rules run early ------------------------------------------------------------------


def _fetch(tables):
    return lambda name: tables.get(name)


@pytest.mark.parametrize(
    "rule,done",
    [(EARLY_OK, 1), (BLOCKED, 0)],
    ids=["early", "blocked"],
)
def test_early_rules_wait_for_the_compute_phase_when_it_reads_their_column(rule, done) -> None:
    s = _with_rule(rule)
    tables = _engine(s, early=False, stream=False).generate().tables
    early = EarlyRules(s, 5, _fetch(tables))
    early.advance()
    assert early.finish()[0] == done


def test_early_rules_apply_in_schema_order() -> None:
    s = _with_rule(EARLY_OK)
    s.business_rules = [BLOCKED, EARLY_OK]  # the first cannot run early, so nothing may
    tables = _engine(s, early=False, stream=False).generate().tables
    early = EarlyRules(s, 5, _fetch(tables))
    early.advance()
    assert early.finish() == (0, {})


def test_early_rules_need_every_table_they_name() -> None:
    s = _with_rule(EARLY_OK)
    tables = _engine(s, early=False, stream=False).generate().tables
    early = EarlyRules(s, 5, _fetch({"order": tables["order"]}))  # no customer yet
    early.advance()
    assert early.finish()[0] == 0
