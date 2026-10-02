"""The deterministic event sequence: rows, resume, out-of-order, anomalies (P5-01)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pyarrow as pa
import pytest

from shape.errors import ShapeError, ShapeSchemaError
from shape.plugins.api.v1 import ChaosReport
from shape.streaming.emit import (
    AnomalyInjector,
    EventPlan,
    ValueAnomalyMutator,
    resolve_mutators,
)
from shape.streaming.emit.formats import FIELD_SEQ, FIELD_TIME

from .conftest import make_engine


def _by_table(plan: EventPlan, offset: int = 0) -> dict[str, pa.Table]:
    """The events as one table per streamed table, in stream order."""
    batches: dict[str, list[pa.RecordBatch]] = {}
    for b in plan.blocks(offset):
        batches.setdefault(b.table, []).append(b.batch)
    return {t: pa.Table.from_batches(bs) for t, bs in batches.items()}


def _events(plan: EventPlan, offset: int = 0) -> pa.Table:
    """The events of a plan over a single table."""
    tables = _by_table(plan, offset)
    assert len(tables) == 1, list(tables)
    return next(iter(tables.values()))


def _rows(plan: EventPlan, offset: int = 0) -> list[tuple[Any, ...]]:
    """Every event in stream order as ``(table, values...)``."""
    return [(b.table, *row.values()) for b in plan.blocks(offset) for row in b.batch.to_pylist()]


def test_rows_equal_shape_generate(retail_engine) -> None:
    """One table the engine reads in chunks (customer) and one it post-processes (order)."""
    whole = make_engine().generate()
    plan = EventPlan(retail_engine, tables=["customer", "order"])
    ev = _by_table(plan)
    for name in ("customer", "order"):
        rows = ev[name]
        expect = whole[name]
        assert rows[FIELD_SEQ].to_pylist() == list(range(expect.num_rows))
        assert rows.select(expect.schema.names).equals(expect), name
        assert rows[FIELD_TIME].equals(
            expect["signup_date" if name == "customer" else "order_date"]
        )


def test_tables_in_dependency_order_and_total(retail_engine) -> None:
    plan = EventPlan(retail_engine)
    assert plan.total_events == sum(retail_engine.row_counts.values()) == 21750
    seen: list[str] = []
    for b in plan.blocks():
        if not seen or seen[-1] != b.table:
            seen.append(b.table)
    assert seen == retail_engine.order


def test_unknown_table(retail_engine) -> None:
    with pytest.raises(ShapeError, match="unknown table 'nope'"):
        EventPlan(retail_engine, tables=["nope"])


@pytest.mark.parametrize("offset", [0, 1, 999, 1000, 4999, 5000, 5001, 21749, 21750])
def test_resume_is_the_suffix(retail_engine, offset: int) -> None:
    kwargs: dict[str, Any] = {"out_of_order": 0.3, "ooo_window": 100}
    full = _rows(EventPlan(retail_engine, **kwargs))
    resumed = _rows(EventPlan(retail_engine, **kwargs), offset)
    assert len(resumed) == len(full) - offset
    assert resumed == full[offset:]


def test_resume_with_anomalies(retail_engine) -> None:
    def plan() -> EventPlan:
        inj = AnomalyInjector(0.1, [ValueAnomalyMutator()], retail_engine.seed)
        return EventPlan(retail_engine, out_of_order=0.2, ooo_window=64, anomaly=inj)

    full = _rows(plan())
    assert _rows(plan(), 7777) == full[7777:]


def test_fingerprint_follows_every_option(retail_engine) -> None:
    base = EventPlan(retail_engine).fingerprint()
    assert EventPlan(retail_engine).fingerprint() == base
    for kwargs in (
        {"out_of_order": 0.1},
        {"ooo_window": 10},
        {"tables": ["customer"]},
        {"envelope": "cloudevents"},
        {"anomaly": AnomalyInjector(0.1, [ValueAnomalyMutator()], 11)},
    ):
        assert EventPlan(retail_engine, **kwargs).fingerprint() != base, kwargs
    assert EventPlan(make_engine(12)).fingerprint() != base


def test_out_of_order_is_a_bounded_permutation(retail_engine) -> None:
    w = 200
    clean = _events(EventPlan(retail_engine, tables=["order"]))
    plan = EventPlan(retail_engine, tables=["order"], out_of_order=0.2, ooo_window=w)
    late = _events(plan)
    assert late.num_rows == clean.num_rows
    seq = np.asarray(late[FIELD_SEQ])
    assert sorted(seq.tolist()) == list(range(clean.num_rows))  # nothing lost or repeated
    assert (seq // w == np.arange(len(seq)) // w).all()  # nobody leaves its window
    behind = seq < np.maximum.accumulate(seq)  # arrives after a later row: delivered late
    assert 0.15 < behind.mean() < 0.25
    # each row still carries its own values: sort back by sequence and compare
    back = late.take(pa.array(np.argsort(seq, kind="stable")))
    assert back.equals(clean)
    # event times are not rewritten, so a late event is behind in event-time order
    t = late[FIELD_TIME].to_numpy()
    assert (np.diff(t.astype("int64")) < 0).any()


def test_out_of_order_zero_changes_nothing_and_is_deterministic(retail_engine) -> None:
    a = _by_table(EventPlan(retail_engine, out_of_order=0.0))
    for t in a.values():
        seq = np.asarray(t[FIELD_SEQ])
        assert (seq == np.arange(len(seq))).all()
    assert _rows(EventPlan(retail_engine, out_of_order=0.5)) == _rows(
        EventPlan(retail_engine, out_of_order=0.5)
    )


def test_out_of_order_is_independent_of_the_block_size(retail_engine) -> None:
    # the window decides the permutation; the block (a multiple of it) does not
    a = _events(EventPlan(retail_engine, tables=["customer"], out_of_order=0.4, ooo_window=300))
    plan = EventPlan(retail_engine, tables=["customer"], out_of_order=0.4, ooo_window=300)
    plan.block_rows = 600
    assert a.equals(_events(plan))


@pytest.mark.parametrize("table", ["customer", "order_line"])
def test_anomalies_touch_about_the_fraction_and_only_values(retail_engine, table: str) -> None:
    f = 0.1
    clean = _events(EventPlan(retail_engine, tables=[table]))
    inj = AnomalyInjector(f, [ValueAnomalyMutator()], retail_engine.seed)
    dirty = _events(EventPlan(retail_engine, tables=[table], anomaly=inj))
    assert dirty.num_rows == clean.num_rows
    assert dirty.schema == clean.schema
    differs = np.zeros(clean.num_rows, bool)
    for name in clean.schema.names:
        if name.endswith("_id") or name.startswith("_shape"):
            assert dirty[name].equals(clean[name]), name  # keys and event fields are not mutated
            continue
        c, d = clean[name].combine_chunks(), dirty[name].combine_chunks()
        same = pa.compute.fill_null(pa.compute.equal(c, d), False)
        both_null = pa.compute.and_(pa.compute.is_null(c), pa.compute.is_null(d))
        differs |= ~np.asarray(pa.compute.or_(same, both_null))
    n = clean.num_rows
    assert abs(inj.stats.rows_selected / n - f) < 0.03
    assert inj.stats.rows_affected == {"value-anomaly": inj.stats.rows_selected}
    assert differs.sum() <= inj.stats.rows_selected
    assert differs.sum() > 0.5 * inj.stats.rows_selected


def test_zero_fraction_is_a_noop(retail_engine) -> None:
    inj = AnomalyInjector(0.0, [ValueAnomalyMutator()], 1)
    a = _events(EventPlan(retail_engine, tables=["customer"], anomaly=inj))
    assert a.equals(_events(EventPlan(retail_engine, tables=["customer"])))
    assert inj.stats.rows_selected == 0


class _Dropper:
    name = "dropper"

    def mutate(self, batch: pa.RecordBatch, seed: int) -> tuple[pa.RecordBatch, ChaosReport]:
        return batch.slice(1), ChaosReport(self.name, 1)


class _Retyper:
    name = "retyper"

    def mutate(self, batch: pa.RecordBatch, seed: int) -> tuple[pa.RecordBatch, ChaosReport]:
        first = batch.schema.names[0]
        out = batch.set_column(0, first, pa.array(["x"] * batch.num_rows))
        return out, ChaosReport(self.name, batch.num_rows)


def test_a_mutator_must_keep_rows_and_schema(retail_engine) -> None:
    for bad, text in ((_Dropper(), "row count"), (_Retyper(), "schema")):
        inj = AnomalyInjector(0.5, [bad], 1)
        with pytest.raises(ShapeSchemaError, match=text):
            _events(EventPlan(retail_engine, tables=["customer"], anomaly=inj))


def test_a_shape_chaos_plugin_mutator_is_used(retail_engine) -> None:
    from shape.plugins.host import default_host

    host = default_host()
    host.register("shape.chaos", "zero-out", _ZeroOut())
    try:
        inj = AnomalyInjector(0.2, resolve_mutators(["zero-out"]), 5)
        ev = _events(EventPlan(retail_engine, tables=["product"], anomaly=inj))
        zeros = pa.compute.sum(pa.compute.equal(ev["unit_price"], 0.0)).as_py()
        assert zeros == inj.stats.rows_selected > 100
    finally:
        from shape.plugins.host import reset_default_host

        reset_default_host()


class _ZeroOut:
    name = "zero-out"

    def mutate(self, batch: pa.RecordBatch, seed: int) -> tuple[pa.RecordBatch, ChaosReport]:
        i = batch.schema.get_field_index("unit_price")
        out = batch.set_column(i, "unit_price", pa.array(np.zeros(batch.num_rows), pa.float64()))
        return out, ChaosReport(self.name, batch.num_rows)


def test_unknown_mutator_names_what_exists() -> None:
    with pytest.raises(ShapeError, match=r"no-such.*available: value-anomaly"):
        resolve_mutators(["no-such"])


def test_fraction_without_mutators_is_an_error() -> None:
    with pytest.raises(ShapeError):
        AnomalyInjector(0.1, [], 1)
    with pytest.raises(ValueError):
        AnomalyInjector(1.5, [ValueAnomalyMutator()], 1)
