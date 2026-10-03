"""AUD-io: regression tests for capture (issues filed by the io audit)."""

from __future__ import annotations

import datetime as dt

import pytest

from shape.capture import capture_columns, capture_rows

COLS = {
    "flag": [True, False, True],
    "when": [dt.datetime(2020, 1, 1), None, dt.datetime(2020, 1, 2)],
    "mixed": [1, "x", 3],
    "n": [1, 2, 3],
}


def test_496_mode_applies_to_every_column():
    exact = capture_columns(COLS, mode="exact")["columns"]
    for name, col in exact.items():
        assert col["error_models"]["cardinality"]["algorithm"] == "exact-hash-table", name
        assert isinstance(col["distinct_estimate"], int), name
    bounded = capture_columns(COLS, mode="bounded")["columns"]
    for name, col in bounded.items():
        assert col["error_models"]["cardinality"]["algorithm"] == "hyperloglog", name
    assert exact["flag"]["distinct_estimate"] == 2 and exact["mixed"]["kind"] == "text"
    # capture_rows stays a bounded pass
    rows = capture_rows([{"flag": True}, {"flag": False}]).columns["flag"]
    assert rows["error_models"]["cardinality"]["algorithm"] == "hyperloglog"


def test_496_an_invalid_mode_is_refused_for_every_input():
    for cols in ({"flag": [True]}, {"n": [1]}, {}):
        with pytest.raises(ValueError, match="mode"):
            capture_columns(cols, mode="nonsense")


def test_500_an_int_beyond_float_range_is_counted_not_a_crash():
    col = capture_rows([{"a": 10**400}, {"a": -(10**400)}, {"a": 1}]).columns["a"]
    assert col["kind"] == "numeric" and col["count"] == 3
    assert col["pos_inf_count"] == 1 and col["neg_inf_count"] == 1 and col["finite_count"] == 1


def test_500_rows_must_be_mappings_and_names_strings():
    with pytest.raises(TypeError, match="mapping"):
        capture_rows([[1, 2]])
    with pytest.raises(TypeError, match="column names are strings"):
        capture_rows([{1: 2}])
    with pytest.raises(TypeError, match="column names are strings"):
        capture_columns({1: [1, 2]})
