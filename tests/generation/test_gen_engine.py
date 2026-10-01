from __future__ import annotations

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pytest
from engine_fixtures import STRATEGIES
from gen_fixtures import schema

from shape.generation.engine import (
    ArrayKeys,
    CircularDependencyError,
    Engine,
    MissingTableError,
    RangeKeys,
    calculate_row_counts,
    dependency_levels,
    order_columns,
    resolve_order,
)
from shape.generation.schema import BusinessRule, GenSchema


def engine(s: GenSchema | None = None, **kw) -> Engine:
    return Engine(s or schema(), strategies=STRATEGIES, **kw)


# ---- row counts --------------------------------------------------------------------------


def _counts_schema(**gen) -> GenSchema:
    s = schema()
    s.generation.scales = {
        "small": {"customer": 10, "order": 50},
        "big": {"customer": 100, "order": 500},
    }
    s.generation.derived_counts = gen.pop("derived", {})
    s.model.date_range = gen.pop("date_range", {})
    return s


def test_counts_use_the_preset_and_default_to_100():
    s = _counts_schema()
    assert calculate_row_counts(s) == {"customer": 10, "order": 50, "order_line": 100}
    s.generation.scale = "big"
    assert calculate_row_counts(s)["order"] == 500


def test_derived_counts():
    s = _counts_schema(
        derived={
            "order_line": {"per_parent": "order", "ratio": 2.5},
            "customer": {"fixed": 7},
        },
    )
    assert calculate_row_counts(s) == {"customer": 7, "order": 50, "order_line": 125}


def test_per_year_counts_and_overrides():
    s = _counts_schema(
        derived={"order_line": {"per_year": 12}},
        date_range={"start": "2022-01-01", "end": "2025-12-31"},
    )
    assert calculate_row_counts(s)["order_line"] == 48
    assert calculate_row_counts(s, {"order_line": 3, "customer": 1}) == {
        "customer": 1,
        "order": 50,
        "order_line": 3,
    }


def test_per_parent_before_the_parent_is_counted_uses_100():
    s = _counts_schema(derived={"order_line": {"per_parent": "ghost", "ratio": 0.5}})
    assert calculate_row_counts(s)["order_line"] == 50


def test_engine_overrides_scale_and_seed_without_touching_the_schema():
    s = schema()
    e = Engine(s, scale="small", seed=99, row_counts={"customer": 3}, strategies=STRATEGIES)
    assert e.row_counts["customer"] == 3 and e.seed == 99 and s.model.seed == 5


# ---- order -------------------------------------------------------------------------------


def test_table_order_and_levels():
    s = schema()
    assert resolve_order(s) == ["customer", "order", "order_line"]
    assert dependency_levels(s) == [["customer"], ["order"], ["order_line"]]


def test_order_is_kahn_with_name_ties():
    s = schema()
    for name in ("zeta", "alpha"):
        s.tables[name] = type(s.tables["customer"])(
            name=name,
            columns={"id": s.tables["customer"].columns["customer_id"]},
            primary_key=["id"],
        )
    assert resolve_order(s) == ["alpha", "customer", "order", "order_line", "zeta"]
    assert dependency_levels(s)[0] == ["alpha", "customer", "zeta"]


def test_relationships_add_dependencies():
    s = schema()
    s.tables["customer"].columns.pop("score")
    s.tables["order"].columns["customer_id"].generator["strategy"] = "sequence"
    assert resolve_order(s) == ["customer", "order", "order_line"]  # via relationship o_c
    s.relationships.append(
        type(s.relationships[0])(
            name="back",
            parent="order_line",
            child="customer",
            parent_columns=["line_id"],
            child_columns=["customer_id"],
        )
    )
    with pytest.raises(CircularDependencyError):
        resolve_order(s)


def test_missing_table_and_self_reference():
    s = schema()
    s.tables["order"].columns["customer_id"].generator["ref"] = "ghost.id"
    with pytest.raises(MissingTableError):
        resolve_order(s)
    s = schema()
    s.tables["customer"].columns["boss"] = type(s.tables["customer"].columns["score"])(
        name="boss",
        type="integer",
        generator={"strategy": "foreign_key", "ref": "customer.customer_id"},
    )
    assert resolve_order(s)[0] == "customer"  # a self-reference is not a cycle


def test_column_order():
    s = schema()
    assert order_columns(s.tables["order"]) == [
        "order_id",
        "customer_id",
        "score",
        "double_score",
        "total",
    ]
    assert order_columns(s.tables["customer"]) == ["customer_id", "name", "score"]


# ---- chunking ----------------------------------------------------------------------------


def _big() -> GenSchema:
    return schema({"customer": 3_000, "order": 150_000, "order_line": 2_000})


def test_output_is_identical_for_chunks_of_1k_64k_and_1m():
    results = [engine(_big(), chunk_rows=c).generate() for c in (1_000, 65_536, 1_000_000)]
    first = results[0]
    assert first.row_counts == {"customer": 3_000, "order": 150_000, "order_line": 2_000}
    for other in results[1:]:
        assert other.generation_order == first.generation_order
        for name in first.tables:
            assert other.tables[name].equals(first.tables[name]), name


def test_random_access_equals_a_slice_of_the_whole():
    e = engine(_big())
    whole = e.generate_table("order")
    for start, n in [(0, 10), (149_990, 10), (50_000, 777), (1, 1), (65_535, 2)]:
        part = e.generate_chunk("order", start, n)
        assert pa.Table.from_batches([part]).equals(whole.slice(start, n)), (start, n)
    # asked backwards, a row range still gives the same rows
    again = [e.generate_chunk("order", s, 100) for s in (90_000, 0, 45_000)]
    assert pa.Table.from_batches([again[1]]).equals(whole.slice(0, 100))


def test_a_chunk_keyed_strategy_is_caught_by_the_chunk_test():
    s = schema({"customer": 10, "order": 3_000, "order_line": 10})
    s.tables["order"].columns["score"].generator = {"strategy": "chunk_keyed"}
    a = engine(s, chunk_rows=500).generate_table("order", 500)
    b = engine(s, chunk_rows=1_000).generate_table("order", 1_000)
    assert not a.equals(b)


def test_rows_are_deterministic_and_seed_dependent():
    a = engine(seed=1).generate()
    assert engine(seed=1).generate().tables["order"].equals(a.tables["order"])
    assert not engine(seed=2).generate().tables["order"].equals(a.tables["order"])


def test_null_rate_is_applied_per_row_and_stable():
    s = schema({"customer": 20_000, "order": 10, "order_line": 10})
    t = engine(s).generate_table("customer")
    rate = t["name"].null_count / t.num_rows
    assert abs(rate - 0.2) < 0.02
    assert t["customer_id"].null_count == 0
    again = engine(s, chunk_rows=777).generate_table("customer", 777)
    assert again["name"].is_null().equals(t["name"].is_null())


def test_columns_come_out_in_generation_order_with_types():
    r = engine().generate()
    assert r["order"].column_names == ["order_id", "customer_id", "score", "double_score", "total"]
    assert r["order"].schema.field("order_id").type == pa.int64()
    assert r.table_names == ["customer", "order", "order_line"]
    assert list(r.tables) == ["customer", "order", "order_line"]
    assert len(r) == 40 + 120 + 300


def test_foreign_keys_hold_and_key_pools():
    e = engine()
    r = e.generate()
    assert r.verify_integrity() == []
    assert isinstance(e.key_pool("order"), RangeKeys)
    assert e.key_pool("customer").take(np.array([0, 2])).to_pylist() == [1000, 1002]
    assert len(e.key_pool("customer")) == 40
    r["order_line"]  # noqa: B018
    bad = r.tables["order_line"].set_column(
        1, "order_id", pa.array([10**6] * r["order_line"].num_rows)
    )
    r.tables["order_line"] = bad
    assert "orphan FK" in r.verify_integrity()[0]


def test_non_sequence_key_pool_materialises_once():
    s = schema()
    s.tables["customer"].columns["customer_id"].generator = {
        "strategy": "distribution",
        "low": 1,
        "high": 2,
    }
    e = engine(s)
    pool = e.key_pool("customer")
    assert isinstance(pool, ArrayKeys) and len(pool) == 40
    assert e.key_pool("customer") is pool


def test_multi_column_strategy_and_internal_columns():
    class Pair:
        name = "composite_foreign_key"

        def generate(self, spec, ctx):
            n = ctx.n_rows
            return {"_cache": pa.array(range(n)), "a": pa.array([1] * n), "b": pa.array([2] * n)}

    s = schema()
    s.tables["order_line"].columns["a"] = type(s.tables["order"].columns["score"])(
        name="a", type="integer", generator={"strategy": "composite_foreign_key"}
    )
    s.tables["order_line"].columns["b"] = type(s.tables["order"].columns["score"])(
        name="b", type="integer", generator={"strategy": "derived", "source": "a"}
    )
    e = Engine(s, strategies={**STRATEGIES, "composite_foreign_key": Pair()})
    t = e.generate_table("order_line")
    assert "_cache" not in t.column_names
    assert t["a"].to_pylist()[:2] == [1, 1] and t["b"].to_pylist()[:2] == [2, 2]


def test_errors():
    unknown = schema()
    for col in unknown.tables["customer"].columns.values():
        col.generator = {"strategy": "no_such_strategy"}
    with pytest.raises(ValueError, match="Unknown strategy"):
        Engine(unknown, strategies={}).generate_chunk("customer", 0, 1)
    with pytest.raises(ValueError, match="outside table"):
        engine().generate_chunk("customer", 30, 20)
    with pytest.raises(ValueError, match="chunk_rows"):
        Engine(schema(), chunk_rows=0)

    class Short:
        name = "short"

        def generate(self, spec, ctx):
            return pa.array([1])

    s = schema()
    s.tables["customer"].columns["score"].generator = {"strategy": "short"}
    with pytest.raises(ValueError, match="returned 1 values"):
        Engine(s, strategies={**STRATEGIES, "short": Short()}).generate_chunk("customer", 0, 5)


def test_empty_table_and_no_generator_column():
    s = schema({"customer": 0, "order": 0, "order_line": 0})
    s.tables["customer"].columns["note"] = type(s.tables["customer"].columns["score"])(
        name="note", type="string", generator={}
    )
    t = engine(s).generate_table("customer")
    assert t.num_rows == 0 and t.schema.field("note").type == pa.string()


def test_no_generator_column_is_all_null():
    s = schema()
    s.tables["customer"].columns["note"] = type(s.tables["customer"].columns["score"])(
        name="note", type="string", generator={}
    )
    t = engine(s).generate_table("customer")
    assert t["note"].null_count == t.num_rows


# ---- post-passes through the engine ------------------------------------------------------


def test_computed_columns_are_filled_after_generation():
    r = engine().generate()
    order, lines = r["order"], r["order_line"]
    assert pc.sum(r["order"]["total"]).as_py() == pytest.approx(
        pc.sum(lines["amount"]).as_py(), abs=0.5
    )
    one = order["order_id"][0].as_py()
    expect = pc.sum(pc.filter(lines["amount"], pc.equal(lines["order_id"], one))).as_py() or 0
    assert order["total"][0].as_py() == pytest.approx(round(expect, 2))
    assert engine().generate_table("order")["total"].null_count == order.num_rows  # placeholder


def test_rule_repair_and_correlation_run_in_generate():
    s = schema()
    s.business_rules.append(
        BusinessRule(name="dbl", type="cross_column", rule="double_score > score", table="order")
    )
    r = engine(s).generate()
    assert r.remaining_violations == []
    s.correlated_columns = {"order": [["score", "double_score", 0.9]]}
    r2 = engine(s).generate()
    a = r2["order"]["score"].to_numpy()
    b = r2["order"]["double_score"].to_numpy()
    assert np.corrcoef(a, b)[0, 1] > 0.5
    assert sorted(a) == sorted(r["order"]["score"].to_numpy())  # marginals untouched


# ---- dry run -----------------------------------------------------------------------------


def test_dry_run_plans_without_generating():
    class Boom:
        name = "boom"

        def generate(self, spec, ctx):
            raise AssertionError("dry run generated a row")

    strategies = {k: Boom() for k in STRATEGIES}
    e = Engine(schema(), strategies=strategies)
    d = e.dry_run()
    assert d.ok and d.order == ["customer", "order", "order_line"]
    assert d.total_rows == 460 and d.estimated_bytes > 0
    assert d.tables["order"]["rows"] == 120
    assert [c["name"] for c in d.tables["order"]["columns"]][0] == "order_id"
    assert "customer" in d.render() and "nothing was generated" in d.render()
    assert d.to_dict()["ok"] is True
    assert list(d.to_dict()["tables"]) == d.order


def test_dry_run_reports_unknown_strategies_and_schema_errors():
    s = schema()
    s.tables["order"].primary_key.append("missing")
    s.tables["customer"].columns["score"].generator = {"strategy": "no_such_strategy"}
    d = Engine(s, strategies={"sequence": STRATEGIES["sequence"]}).dry_run()
    assert not d.ok
    assert any("no_such_strategy" in m for m in d.missing_strategies)
    assert any(i.level == "error" for i in d.issues)
    assert "not ok" in d.render()


def test_the_documented_interface_is_the_one_that_exists():
    """Every public name of the engine contract appears in docs/GENERATION_ENGINE.md."""
    from pathlib import Path

    doc = (Path(__file__).resolve().parents[2] / "docs" / "GENERATION_ENGINE.md").read_text("utf-8")
    for name in (
        "GenSchema", "to_dict", "from_dict", "validate", "resolve_order", "dependency_levels",
        "calculate_row_counts", "order_columns", "dry_run", "generate_chunk", "iter_chunks",
        "generate", "RowStream", "EngineContext", "key_pool", "RangeKeys", "ArrayKeys",
        "CircularDependencyError", "MissingTableError", "validate_rules", "fix_rules",
        "generation-schema-v1.json",
    ):  # fmt: skip
        assert name in doc, name
    for name in ("generate_chunk", "iter_chunks", "key_pool", "dry_run", "generate_table"):
        assert callable(getattr(Engine, name))
