"""AUD-chaos regressions for row anomalies and the value category."""

from __future__ import annotations

import re

import numpy as np
import pyarrow as pa
import pytest

from shape.chaos import ChaosConfig, ChaosOverride, ValueChaosMutator, inject_anomalies


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


@pytest.mark.parametrize(
    ("config", "message"),
    [
        (
            ChaosConfig(categories={"value": {"enabled": True, "weight": "high"}}),
            r"category 'value': weight is a number 0 or more, got 'high'",
        ),
        (
            ChaosConfig(categories={"value": {"enabled": True, "weight": -1}}),
            r"category 'value': weight is a number 0 or more, got -1",
        ),
        (
            ChaosConfig(categories={"value": True}),  # type: ignore[dict-item]
            r"category 'value' is a mapping",
        ),
        (ChaosConfig(seed=-1), r"seed is an integer 0 or more, got -1"),
        (
            ChaosConfig(overrides=[ChaosOverride(10, "nope")]),
            r"override on day 10: unknown category 'nope'",
        ),
    ],
)
def test_chaos_config_validate_lists_bad_settings(config: ChaosConfig, message: str) -> None:
    """#414: validate() lists what would crash or silently never fire at run time."""
    errors = config.validate()
    assert any(re.search(message, e) for e in errors), errors


def test_chaos_config_validate_accepts_the_defaults() -> None:
    assert ChaosConfig().validate() == []
