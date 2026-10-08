"""Regression tests for drift defects found by the AUD-quality audit."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from shape.drift import compare
from shape.drift.engine import resolve_policy


def _model(rows, **col):
    column = {
        "name": "x",
        "arrow_type": "int64",
        "kind": "float",
        "count": rows,
        "null_count": 0,
        "error_models": {},
        **col,
    }
    return {
        "schema_version": 2,
        "engine": "t",
        "mode": "exact",
        "tables": {"t": {"name": "t", "rows": rows, "columns": [column]}},
    }


# #469: min_rows = 0 is allowed, and an empty column then divided by zero
def test_min_rows_zero_with_empty_columns_does_not_crash():
    stats = {"variance_sample": 1.0, "outlier_rate": 0.0}
    a = _model(0, mean=1.0, quantiles={"0.5": 1.0}, min=0.0, max=2.0, **stats)
    b = _model(0, mean=5.0, quantiles={"0.5": 5.0}, min=4.0, max=6.0, **stats)
    kinds = {d.kind for d in compare(a, b, thresholds={"min_rows": 0})}
    assert "distribution_shift" not in kinds and "outlier_rate_change" not in kinds


# #470: a NaN threshold was accepted (and silently turned its comparison off); a malformed
# policy crashed with AttributeError or TypeError
def test_a_nan_threshold_is_refused():
    with pytest.raises(ValueError, match="null_rate"):
        resolve_policy({"null_rate": float("nan")})
    with pytest.raises(ValueError, match="null_rate"):
        resolve_policy(column_thresholds={"x": {"null_rate": float("nan")}})


def test_an_infinite_threshold_turns_its_comparison_off():
    a, b = _model(100), _model(1000)
    assert [d.kind for d in compare(a, b)] == ["row_count_change"]
    assert compare(a, b, thresholds={"row_count_ratio_max": float("inf")}) == []


@pytest.mark.parametrize(
    "policy",
    [
        {"columns": ["x"]},
        {"columns": "x"},
        {"thresholds": ["x"]},
        {"thresholds": 3},
        {"columns": {"x": 1}},
    ],
)
def test_a_malformed_policy_is_a_value_error(policy):
    with pytest.raises(ValueError, match="policy|thresholds"):
        resolve_policy(policy=policy)


# #471: documents with no table gave "not enough values to unpack"
def test_a_document_without_tables_is_named_in_the_error():
    with pytest.raises(ValueError, match="no table"):
        compare({"tables": {}}, {"tables": {}})


# #478: a naive min/max was read in the machine's time zone
def test_the_span_of_a_naive_temporal_column_does_not_depend_on_the_time_zone():
    code = (
        "from shape.drift.engine import _days;"
        "print(_days('2026-03-20T12:00:00') - _days('2026-03-06T12:30:00'))"
    )
    spans = set()
    for tz in ("UTC", "America/New_York", "Asia/Kolkata"):
        env = {**os.environ, "TZ": tz}
        out = subprocess.run(
            [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True
        )
        spans.add(out.stdout.strip())
    assert len(spans) == 1, spans
