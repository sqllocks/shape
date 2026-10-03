"""W1-08 (#66): ``row_count_change``, a table whose row count moved beyond a threshold."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest

import shape
from shape.drift.engine import DEFAULT_THRESHOLDS, KIND_SEVERITY
from shape.streaming.monitor import ShapeMonitor


def table(rows: int, seed: int = 0) -> pa.Table:
    rng = np.random.default_rng(seed)
    return pa.table({"id": np.arange(rows), "x": rng.normal(0, 1, rows)})


def row_changes(diff):
    return [c for c in diff.changes if c["kind"] == "row_count_change"]


def test_kind_is_registered_with_documented_defaults():
    assert KIND_SEVERITY["row_count_change"] == "medium"
    assert DEFAULT_THRESHOLDS["row_count_ratio_max"] == 2.0
    assert DEFAULT_THRESHOLDS["row_count_ratio_min"] == 0.5


@pytest.mark.parametrize(
    ("before", "after"),
    [(1000, 2500), (1000, 2100), (1000, 400), (1000, 450), (200, 1000)],
)
def test_a_row_count_beyond_the_ratio_is_reported(before, after):
    ch = row_changes(shape.diff(shape.profile(table(before)), shape.profile(table(after, 1))))
    assert len(ch) == 1
    c = ch[0]
    assert (c["baseline"], c["current"]) == (before, after)
    assert c["column"] is None and c["severity"] == "medium"
    ratio = after / before
    assert c["score"] == pytest.approx(round(1 - min(ratio, 1 / ratio), 4))


@pytest.mark.parametrize(
    ("before", "after"), [(1000, 1000), (1000, 2000), (1000, 500), (1000, 1200), (2600, 4000)]
)
def test_a_row_count_inside_the_ratio_is_quiet(before, after):
    # boundary: exactly 2x and exactly half are not beyond the thresholds
    d = shape.diff(shape.profile(table(before)), shape.profile(table(after, 1)))
    assert row_changes(d) == []


def test_zero_rows_to_some_rows_scores_one_and_zero_to_zero_is_quiet():
    empty = pa.table({"id": pa.array([], pa.int64()), "x": pa.array([], pa.float64())})
    ch = row_changes(shape.diff(shape.profile(empty), shape.profile(table(50))))
    assert [c["score"] for c in ch] == [1.0]
    assert row_changes(shape.diff(shape.profile(empty), shape.profile(empty))) == []


def test_thresholds_tune_and_disable_it():
    a, b = shape.profile(table(1000)), shape.profile(table(1200, 1))
    assert row_changes(shape.diff(a, b)) == []
    assert len(row_changes(shape.diff(a, b, thresholds={"row_count_ratio_max": 1.1}))) == 1
    c = shape.profile(table(300, 2))
    assert len(row_changes(shape.diff(a, c))) == 1
    assert row_changes(shape.diff(a, c, thresholds={"row_count_ratio_min": 0.2})) == []
    assert row_changes(shape.diff(a, c, ignore_columns=["x"])) != []  # a table-level change
    assert shape.diff(a, c, thresholds={"min_severity": "high"}).changes == []


def test_a_dataset_names_the_table():
    a = shape.profile({"orders": table(1000), "items": table(1000)})
    b = shape.profile({"orders": table(1000, 1), "items": table(3000, 1)})
    ch = row_changes(shape.diff(a, b))
    assert len(ch) == 1 and ch[0]["column"] is None
    drifts = shape.drift.compare(a, b)
    assert any(d.kind == "row_count_change" for d in drifts)


def test_bad_threshold_values_raise():
    p = shape.profile(table(10))
    with pytest.raises(ValueError):
        shape.diff(p, p, thresholds={"row_count_ratio_max": -1})


def test_a_monitors_buffer_size_is_not_a_row_count_change():
    reference = shape.profile(table(5000))
    mon = ShapeMonitor(reference, every=200)
    rng = np.random.default_rng(3)
    event = None
    for i in range(200):
        event = mon.add({"id": i, "x": float(rng.normal(0, 1))}) or event
    assert event is not None
    assert all(d.kind != "row_count_change" for d in event.drifts)


def test_a_stream_window_is_not_compared_by_size():
    from datetime import datetime, timedelta

    from shape.streaming import TumblingProfiler

    schema = pa.schema([("_shape_event_time", pa.timestamp("us")), ("x", pa.float64())])
    n = 1200
    t0 = datetime(2026, 1, 1)
    rng = np.random.default_rng(11)
    batch = pa.record_batch(
        {
            "_shape_event_time": pa.array(
                [t0 + timedelta(milliseconds=100 * i) for i in range(n)], pa.timestamp("us")
            ),
            "x": pa.array(rng.normal(0, 1, n).tolist()),
        },
        schema=schema,
    )
    profiler = TumblingProfiler(schema, timedelta(minutes=1))
    window = (list(profiler.process(batch)) + list(profiler.finish()))[0]
    stored = shape.profile(table(20000, 4))  # 20000 rows against a window of about 600
    assert row_changes(shape.diff(stored, window)) == []
    assert row_changes(shape.diff(window, stored)) == []
