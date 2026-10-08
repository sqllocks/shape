"""The ``bootstrap`` generation strategy: rows resampled from a source dataset."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest

from shape.generation import reference
from shape.generation.engine import Engine
from shape.generation.schema import GenSchema
from shape.generation.strategy_kit import StrategyError

SOURCE = pa.table(
    {
        "age": pa.array(np.arange(100, 200, dtype=np.int64)),
        "income": pa.array(np.linspace(1000.0, 9000.0, 100)),
        "city": pa.array([f"c{i}" for i in range(100)]),
        "const": pa.array([3] * 100),
    }
)


def _col(name: str, **gen):
    return {
        "name": name,
        "type": gen.pop("type", "float"),
        "generator": {"strategy": "bootstrap", **gen},
    }


def _engine(rows=500, seed=1, **overrides) -> Engine:
    cols = {
        "age": _col("age", type="float", dataset="people", field="age", jitter=0),
        "income": _col("income", dataset="people", field="income", jitter=0),
        "city": _col("city", type="string", dataset="people", field="city"),
        "jittered": _col("jittered", dataset="people", field="income", jitter=0.05),
        "const": _col("const", type="integer", dataset="people", field="const"),
        "other": _col("other", type="string", dataset="people", field="city"),
    }
    cols.update(overrides)
    doc = {
        "schema_version": 1,
        "model": {"name": "b", "seed": seed},
        "tables": {"t": {"name": "t", "primary_key": [], "columns": cols}},
        "relationships": [],
        "generation": {"scale": "small", "scales": {"small": {"t": rows}}},
    }
    return Engine(GenSchema.from_dict(doc), seed=seed)


@pytest.fixture(autouse=True)
def _dataset():
    reference.register_dataset("people", SOURCE)
    yield
    reference.unregister_dataset("people")


def test_columns_share_the_source_row_and_values_come_from_the_source():
    t = _engine().generate_table("t")
    ages = t["age"].to_pylist()
    assert set(ages) <= set(range(100, 200))
    row = {a: i for i, a in enumerate(range(100, 200))}
    for age, income, city in zip(ages, t["income"].to_pylist(), t["city"].to_pylist(), strict=True):
        assert income == pytest.approx(SOURCE["income"][row[age]].as_py())
        assert city == f"c{row[age]}"
    assert t["other"].to_pylist() == t["city"].to_pylist()  # the same dataset: the same rows


def test_rows_are_drawn_with_replacement_uniformly():
    t = _engine(rows=20000).generate_table("t")
    counts = np.bincount(np.array(t["age"].to_pylist()) - 100, minlength=100)
    assert counts.min() > 0 and counts.max() < 2.5 * counts.mean()


def test_jitter_is_a_fraction_of_the_source_spread_and_defaults_to_one_percent():
    t = _engine(rows=20000).generate_table("t")
    base = np.array(t["income"].to_pylist())
    jit = np.array(t["jittered"].to_pylist()) - base
    std = SOURCE["income"].to_numpy().std(ddof=1)
    assert jit.std() == pytest.approx(0.05 * std, rel=0.05) and abs(jit.mean()) < 0.01 * std
    one = _engine(
        rows=20000, jittered=_col("jittered", dataset="people", field="income")
    ).generate_table("t")
    assert (np.array(one["jittered"].to_pylist()) - base).std() == pytest.approx(
        0.01 * std, rel=0.05
    )


def test_no_jitter_for_text_or_a_constant_column_and_integers_become_floats():
    t = _engine().generate_table("t")
    assert t["city"].type == pa.string() and t["const"].to_pylist() == [3] * 500
    assert t["age"].type == pa.int64() and t["const"].type == pa.int64()  # no jitter asked
    j = _engine(age_j=_col("age_j", dataset="people", field="age", jitter=0.1)).generate_table("t")
    assert j["age_j"].type == pa.float64()


def test_deterministic_chunk_independent_and_seeded():
    e = _engine(rows=1000)
    whole = e.generate_table("t", chunk_rows=1000)
    assert e.generate_table("t", chunk_rows=77).equals(whole)
    for name in ("age", "jittered", "city"):
        piece = e.generate_chunk("t", 300, 50).column(name)
        assert piece.equals(whole.column(name).combine_chunks().slice(300, 50))
    assert _engine(rows=1000).generate_table("t").equals(whole)
    assert not _engine(rows=1000, seed=2).generate_table("t").equals(whole)


def test_nulls_in_the_source_stay_null():
    reference.register_dataset("sparse", pa.table({"v": pa.array([1.0, None, 3.0, None, 5.0])}))
    try:
        t = _engine(
            rows=200, only=_col("only", dataset="sparse", field="v", jitter=0.1)
        ).generate_table("t")
    finally:
        reference.unregister_dataset("sparse")
    vals = t["only"].to_pylist()
    assert 0 < vals.count(None) < 200


def test_errors_name_the_column():
    with pytest.raises(StrategyError, match="requires 'dataset'.*t.x"):
        _engine(x=_col("x", field="age")).generate_table("t")
    with pytest.raises(StrategyError, match="requires 'field'.*t.x"):
        _engine(x=_col("x", dataset="people")).generate_table("t")
    with pytest.raises(StrategyError, match="Field 'nope' not found.*t.x"):
        _engine(x=_col("x", dataset="people", field="nope")).generate_table("t")
    with pytest.raises(StrategyError, match="jitter"):
        _engine(x=_col("x", dataset="people", field="age", jitter=-1)).generate_table("t")
    with pytest.raises(StrategyError, match="not registered"):
        _engine(x=_col("x", dataset="missing", field="age")).generate_table("t")


def test_is_a_registered_builtin_strategy():
    from shape.plugins.host import default_host

    assert default_host().get("shape.strategies", "bootstrap").name == "bootstrap"
