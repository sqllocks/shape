"""HUNT2-scenario: regression tests for chaos defects found in the second audit."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest

from shape.chaos import ValueChaosMutator

BIG = [
    pytest.param(pa.array([2**60 + i for i in range(40)]), id="int64-above-2**53"),
    pytest.param(pa.array([2**53 + 1 + i for i in range(40)]), id="int64-just-above-2**53"),
    pytest.param(pa.array([-(2**63)] * 40), id="int64-minimum"),
    pytest.param(pa.array([2**64 - 1 - i for i in range(40)], pa.uint64()), id="uint64-maximum"),
]


@pytest.mark.parametrize("kind", ["out_of_range", "negative_amounts"])
@pytest.mark.parametrize("column", BIG)
def test_661_value_chaos_changes_a_large_integer_column(kind, column):
    table = pa.table({"id": column})
    out, events = ValueChaosMutator().apply_one(kind, table, np.random.default_rng(0), 1.0)
    assert out.num_rows == table.num_rows
    assert events and events[0].kind == kind and events[0].rows > 0
    # the column becomes float64, so a value above 2**53 may round; only the rows the event counts
    # are changed by more than that rounding
    far = sum(
        abs(float(a) - float(b)) > 1e-9 * abs(float(b))
        for a, b in zip(out["id"].to_pylist(), column.to_pylist(), strict=True)
    )
    assert 0 < far <= events[0].rows


def test_661_a_small_integer_column_is_unchanged_in_behaviour():
    table = pa.table({"id": pa.array(range(1, 41))})
    out, events = ValueChaosMutator().apply_one(
        "out_of_range", table, np.random.default_rng(0), 1.0
    )
    peak = 40
    changed = [v for v in out["id"].to_pylist() if v is not None and v > peak]
    assert changed and all(100 * peak <= v <= 1000 * peak for v in changed)
    assert pa.types.is_floating(out.schema.field("id").type)
