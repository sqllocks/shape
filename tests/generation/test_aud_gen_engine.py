"""AUD-gen: the engine's own passes (output types, null rates, row counts)."""

from __future__ import annotations

import datetime as dt

import pyarrow as pa  # type: ignore[import-untyped]

from shape.generation.engine import cast_output
from shape.generation.schema import Column


def test_a_timestamp_is_cut_down_to_its_precision_before_1970_too():
    # 188: pc.divide truncates toward zero, so 1969-12-31 23:59:59.5 became 1970-01-01 00:00:00.
    col = Column("t", "timestamp", {}, precision=0)
    values = pa.array(
        [
            dt.datetime(1969, 12, 31, 23, 59, 59, 500000),
            dt.datetime(1955, 6, 1, 5, 22, 24, 641277),
            None,
            dt.datetime(2020, 1, 1, 0, 0, 0, 700000),
        ],
        pa.timestamp("us"),
    )
    assert cast_output(values, "timestamp", "t.t", col).to_pylist() == [
        dt.datetime(1969, 12, 31, 23, 59, 59),
        dt.datetime(1955, 6, 1, 5, 22, 24),
        None,
        dt.datetime(2020, 1, 1, 0, 0, 0),
    ]
