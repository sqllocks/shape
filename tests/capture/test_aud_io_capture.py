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
