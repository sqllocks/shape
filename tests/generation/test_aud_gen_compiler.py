"""AUD-gen: generating from captured Shape evidence (``compiler``, #206)."""

from __future__ import annotations

import math

import pytest

from shape.generation import generate_from_shape, generate_relational

_NUM = {
    "kind": "numeric",
    "count": 1000,
    "mean": 50,
    "variance_population": 400,
    "min": 0,
    "max": 100,
}


def test_a_parent_key_from_a_value_column_is_made_unique():
    # 206: the parent's numeric "id" shape was used as the key: 91 distinct float keys of 100.
    parent = {"rows": 100, "columns": {"id": dict(_NUM)}}
    child = {"rows": 1000, "columns": {"x": dict(_NUM)}}
    out = generate_relational(
        {"p": parent, "c": child},
        {"p": 100, "c": 1000},
        [{"parent": "p", "child": "c", "parent_key": "id", "child_fk": "pid"}],
        3,
    )
    keys = list(out["p"]["id"])
    assert len(set(keys)) == len(keys) == 100
    assert set(out["c"]["pid"]) <= set(keys)


def test_a_correlated_target_keeps_its_nulls_and_bounds():
    # 206: the target lost its 100 nulls and its 0..100 bounds (values -30.2..108.6).
    shape = {
        "rows": 1000,
        "columns": {"x": dict(_NUM), "y": {**_NUM, "null_count": 100}},
    }
    cols, report = generate_from_shape(
        shape, seed=1, relationships={"correlations": [{"source": "x", "target": "y", "rho": 0.9}]}
    )
    y = list(cols["y"])
    nulls = [v for v in y if v is None or (isinstance(v, float) and math.isnan(v))]
    values = [v for v in y if v is not None and not (isinstance(v, float) and math.isnan(v))]
    assert len(nulls) == 100
    assert 0 <= min(values) and max(values) <= 100
    assert "correlation:x:y" in report.preserved


def test_a_correlation_with_a_constant_column_is_reported_degraded():
    # 206: with a zero-variance side the correlation was in neither list.
    shape = {"rows": 50, "columns": {"x": {**_NUM, "variance_population": 0}, "y": dict(_NUM)}}
    _, report = generate_from_shape(
        shape, relationships={"correlations": [{"source": "x", "target": "y", "rho": 0.5}]}
    )
    assert "correlation:x:y" in report.degraded


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"n": -1}, "row count"),
        ({"relationships": {"foreign_keys": [{"field": "f", "parent_count": 0}]}}, "parent_count"),
    ],
)
def test_bad_input_says_what_is_wrong(kwargs, message):
    # 206: ValueError("n") and numpy's "high <= 0".
    with pytest.raises(ValueError, match=message):
        generate_from_shape({"rows": 5, "columns": {"x": dict(_NUM)}}, **kwargs)
