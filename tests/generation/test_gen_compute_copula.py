from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest
from gen_fixtures import schema

from shape.generation.compute import apply_compute_phase
from shape.generation.correlation import apply_copula


def _tables():
    return {
        "customer": pa.table({"customer_id": [1, 2, 3]}),
        "order": pa.table(
            {
                "order_id": [1, 2, 3, 4],
                "customer_id": [1, 1, 2, 3],
                "total": pa.nulls(4, pa.float64()),
                "n": pa.nulls(4, pa.float64()),
                "paid": pa.nulls(4, pa.float64()),
            }
        ),
        "order_line": pa.table(
            {
                "order_id": [1, 1, 2, 2, 2, 9],
                "amount": [1.005, 2.0, 3.0, 4.0, 5.0, 7.0],
                "qty": [1, 2, 3, 4, 5, 6],
            }
        ),
    }


def _schema(rule: str, source: str = "amount"):
    s = schema()
    s.tables["order"].columns["total"].generator = {
        "strategy": "computed",
        "rule": rule,
        "child_table": "order_line",
        "child_column": source,
    }
    return s


@pytest.mark.parametrize(
    "rule,expected",
    [
        ("sum_children", [3.0, 12.0, 0.0, 0.0]),
        ("count_children", [2, 3, 0, 0]),
        ("avg_children", [1.5, 4.0, 0.0, 0.0]),
        ("min_children", [1.0, 3.0, 0.0, 0.0]),
        ("max_children", [2.0, 5.0, 0.0, 0.0]),
    ],
)
def test_aggregates_fill_childless_parents_with_zero(rule, expected):
    out = apply_compute_phase(_tables(), _schema(rule))
    assert out["order"]["total"].to_pylist() == pytest.approx(expected)
    assert out["order"].column_names == _tables()["order"].column_names


def test_sum_rounds_to_two_places_and_integers_stay_integer():
    out = apply_compute_phase(_tables(), _schema("sum_children"))
    assert out["order"]["total"].to_pylist()[0] == 3.0  # 1.005 + 2.0 = 3.005 -> 3.0 (half to even)
    ints = apply_compute_phase(_tables(), _schema("sum_children", "qty"))
    assert ints["order"]["total"].type == pa.int64()
    assert ints["order"]["total"].to_pylist() == [3, 12, 0, 0]


def test_inputs_are_not_changed_and_missing_pieces_are_skipped():
    tables = _tables()
    before = tables["order"]
    apply_compute_phase(tables, _schema("sum_children"))
    assert tables["order"].equals(before)
    assert apply_compute_phase({"order": tables["order"]}, _schema("sum_children"))["order"].equals(
        before
    )
    s = _schema("sum_children", "nope")
    assert apply_compute_phase(tables, s)["order"].equals(before)
    with pytest.raises(ValueError, match="Unknown computed rule"):
        apply_compute_phase(tables, _schema("median_children"))


def test_lookup_parent_copies_from_the_parent():
    s = schema()
    s.tables["order_line"].columns["parent_total"] = type(s.tables["order"].columns["total"])(
        name="parent_total",
        type="float",
        generator={
            "strategy": "computed",
            "rule": "lookup_parent",
            "child_table": "order",
            "child_column": "score",
        },
    )
    t = {
        "order": pa.table({"order_id": [1, 2], "score": [1.234, 5.0]}),
        "order_line": pa.table({"order_id": [2, 1, 2], "parent_total": pa.nulls(3, pa.float64())}),
    }
    out = apply_compute_phase(t, s)
    assert out["order_line"]["parent_total"].to_pylist() == [5.0, 1.23, 5.0]


def _ranks(x):
    return np.argsort(np.argsort(x, kind="stable"), kind="stable").astype(float)


def _corr_table(n=20_000):
    rng = np.random.default_rng(0)
    return pa.table(
        {
            "id": np.arange(n),
            "a": rng.uniform(0, 10, n),
            "b": rng.exponential(2.0, n),
            "c": rng.integers(0, 100, n),
            "label": ["x"] * n,
        }
    )


def test_copula_induces_correlation_and_preserves_marginals():
    t = _corr_table()
    out = apply_copula(t, [["a", "b", 0.8], ["a", "c", -0.6]], seed=3, table_name="t")
    a, b, c = (_ranks(out[x].to_numpy()) for x in "abc")
    # A Gaussian copula with parameter r gives Spearman correlation 6/pi * asin(r/2).
    assert np.corrcoef(a, b)[0, 1] == pytest.approx(6 / np.pi * np.arcsin(0.4), abs=0.04)
    assert np.corrcoef(a, c)[0, 1] == pytest.approx(6 / np.pi * np.arcsin(-0.3), abs=0.04)
    for col in "abc":
        assert sorted(out[col].to_pylist()) == sorted(t[col].to_pylist())
    assert out["id"].equals(t["id"]) and out["label"].equals(t["label"])
    assert out["c"].type == t["c"].type


def test_copula_is_deterministic_and_seed_dependent():
    t = _corr_table(2000)
    pairs = [["a", "b", 0.7]]
    one = apply_copula(t, pairs, 1, "t")
    assert one.equals(apply_copula(t, pairs, 1, "t"))
    assert not one.equals(apply_copula(t, pairs, 2, "t"))


def test_copula_leaves_weak_pairs_keys_and_nulls_alone():
    t = _corr_table(500)
    assert apply_copula(t, [["a", "b", 0.2]], 1, "t").equals(t)  # below the threshold
    assert apply_copula(t, [["id", "a", 0.9]], 1, "t").equals(t)  # key-like column
    assert apply_copula(t, [["a", "label", 0.9]], 1, "t").equals(t)  # not numeric
    assert apply_copula(t, [["a", "zzz", 0.9]], 1, "t").equals(t)
    assert apply_copula(t, [], 1, "t").equals(t)
    with_null = t.set_column(1, "a", pa.array([None] + t["a"].to_pylist()[1:]))
    out = apply_copula(with_null, [["a", "b", 0.9]], 1, "t")
    assert out["a"].equals(with_null["a"]) and not out["b"].equals(with_null["b"])
