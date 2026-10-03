"""AUD-chaos regressions for row anomalies and the value category."""

from __future__ import annotations

import numpy as np
import pyarrow as pa

from shape.chaos import ValueChaosMutator, inject_anomalies


def test_anomaly_out_of_range_on_a_huge_float_column() -> None:
    """#406: 100 to 1000 times a peak near the float maximum stays finite, no OverflowError."""
    batch = pa.record_batch({"x": [1e308, 2.0, 3.0]})
    out = inject_anomalies(batch, fraction=1, seed=1, kinds=["out_of_range"])
    assert len(out.rows) == 3
    assert all(np.isfinite(v) for v in out.batch.column(0).to_pylist())


def test_value_out_of_range_on_a_column_holding_infinity() -> None:
    """#406: an infinite peak does not crash the value category."""
    table = pa.table({"x": [1.0, float("inf")] * 10})
    out, events = ValueChaosMutator().apply_one(
        "out_of_range", table, np.random.default_rng(0), 1.0
    )
    assert events[0].kind == "out_of_range"
    assert out.num_rows == 20


def test_value_out_of_range_on_a_huge_finite_column() -> None:
    table = pa.table({"x": [1.0, 1e306] * 10})
    out, _ = ValueChaosMutator().apply_one("out_of_range", table, np.random.default_rng(0), 1.0)
    assert out.num_rows == 20
