"""shape.diff: every drift kind with its default threshold, pass and fail."""

from __future__ import annotations

import pyarrow as pa
import pytest

import shape


def kinds(d, column=None):
    return {c["kind"] for c in d.changes if column is None or c["column"] == column}


def base_table(n=1000, **overrides):
    import numpy as np

    rng = np.random.default_rng(3)
    cols = {
        "id": np.arange(n, dtype=np.int64),
        "amount": rng.normal(100, 10, n),
        "status": rng.choice(["a", "b", "c"], n),
        "note": np.where(rng.random(n) < 0.05, None, "x").astype(object),
    }
    cols.update(overrides)
    return pa.table({k: pa.array(v) for k, v in cols.items()})


def test_identical_profiles_do_not_drift():
    p = shape.profile(base_table())
    d = shape.diff(p, p)
    assert not d.drifted and d.changes == []
    assert d.to_dict() == {"drifted": False, "changes": []}


def test_column_added_and_removed_are_high():
    a = shape.profile(base_table())
    b_tbl = base_table().drop_columns(["status"]).append_column("extra", pa.array([1] * 1000))
    d = shape.diff(a, shape.profile(b_tbl))
    by = {(c["kind"], c["column"]): c for c in d.changes}
    assert by[("column_removed", "status")]["severity"] == "high"
    assert by[("column_added", "extra")]["severity"] == "high"
    assert d.drifted


def test_dtype_change_is_high():
    a = shape.profile(base_table())
    b = shape.profile(base_table(amount=[f"s{i}" for i in range(1000)]))
    d = shape.diff(a, b)
    ch = [c for c in d.changes if c["kind"] == "dtype_change"]
    assert ch and ch[0]["severity"] == "high"
    assert (ch[0]["baseline"], ch[0]["current"]) == ("float", "string")


def test_null_rate_change_threshold():
    import numpy as np

    def with_null_rate(rate):
        rng = np.random.default_rng(9)
        vals = np.where(rng.random(2000) < rate, None, "x").astype(object)
        return shape.profile(pa.table({"c": pa.array(vals)}))

    base = with_null_rate(0.05)
    small = shape.diff(base, with_null_rate(0.08))
    assert "null_rate_change" not in kinds(small)
    big = shape.diff(base, with_null_rate(0.20))
    ch = [c for c in big.changes if c["kind"] == "null_rate_change"]
    assert ch and ch[0]["severity"] == "medium"
    tuned = shape.diff(base, with_null_rate(0.08), thresholds={"null_rate": 0.01})
    assert "null_rate_change" in kinds(tuned)


def test_cardinality_ratio_thresholds():
    a = shape.profile(pa.table({"k": [i % 100 for i in range(1000)]}))
    up = shape.profile(pa.table({"k": [i % 200 for i in range(1000)]}))
    down = shape.profile(pa.table({"k": [i % 50 for i in range(1000)]}))
    near = shape.profile(pa.table({"k": [i % 120 for i in range(1000)]}))
    assert "cardinality_change" in kinds(shape.diff(a, up))
    assert "cardinality_change" in kinds(shape.diff(a, down))
    assert "cardinality_change" not in kinds(shape.diff(a, near))


def test_mean_shift_threshold():
    a = shape.profile(base_table())
    small = shape.profile(base_table(amount=base_table().column("amount").to_numpy() + 2.0))
    large = shape.profile(base_table(amount=base_table().column("amount").to_numpy() + 8.0))
    assert "mean_shift" not in kinds(shape.diff(a, small))  # 2 < 0.5 * 10
    ch = [c for c in shape.diff(a, large).changes if c["kind"] == "mean_shift"]
    assert ch and ch[0]["severity"] == "medium"
    assert "mean_shift" in kinds(shape.diff(a, small, thresholds={"mean_shift_std": 0.1}))


def test_distribution_family_change_is_low():
    import numpy as np

    rng = np.random.default_rng(5)
    a = shape.profile(pa.table({"v": rng.normal(50, 5, 3000)}))
    b = shape.profile(pa.table({"v": rng.uniform(0, 100, 3000)}))
    ch = [c for c in shape.diff(a, b).changes if c["kind"] == "distribution_change"]
    assert ch and ch[0]["severity"] == "low"
    assert (ch[0]["baseline"], ch[0]["current"]) == ("normal", "uniform")


def test_new_categorical_values_is_low():
    a = shape.profile(pa.table({"s": ["placed", "shipped"] * 50}))
    b = shape.profile(pa.table({"s": ["placed", "shipped", "lost"] * 50}))
    ch = [c for c in shape.diff(a, b).changes if c["kind"] == "new_categorical_values"]
    assert ch and ch[0]["severity"] == "low"
    assert "lost" in ch[0]["current"] and "lost" not in ch[0]["baseline"]
    assert "new_categorical_values" not in kinds(shape.diff(b, a))


def test_min_severity_threshold_filters():
    # one new value in 101 rows: a low-severity change; a third of the rows would also be a
    # (medium) category_shift
    a = shape.profile(pa.table({"s": ["placed", "shipped"] * 50}))
    b = shape.profile(pa.table({"s": ["placed", "shipped"] * 50 + ["lost"]}))
    assert shape.diff(a, b).drifted
    assert not shape.diff(a, b, thresholds={"min_severity": "medium"}).drifted


def test_bad_thresholds_raise():
    p = shape.profile(base_table())
    with pytest.raises(ValueError):
        shape.diff(p, p, thresholds={"nope": 1})
    with pytest.raises(ValueError):
        shape.diff(p, p, thresholds={"min_severity": "urgent"})


def test_multi_table_diff(orders, customers):
    a = shape.profile({"orders": orders, "customer": customers})
    b = shape.profile({"orders": orders})
    d = shape.diff(a, b)
    assert [c["kind"] for c in d.changes] == ["table_removed"]
    same = shape.diff(a, a)
    assert not same.drifted
    c = shape.profile({"orders": orders.drop_columns(["status"]), "customer": customers})
    ch = shape.diff(a, c).changes
    assert ch[0]["column"] == "orders.status" and ch[0]["kind"] == "column_removed"


def test_shape_diff_stays_callable_after_importing_the_legacy_subpackage():
    import importlib

    p = shape.profile(base_table())
    importlib.import_module("shape.diff")  # rebinds the attribute shape.diff to the subpackage
    assert shape.diff(p, p).drifted is False
    from shape.diff import Delta  # the legacy subpackage still imports

    assert Delta is not None
