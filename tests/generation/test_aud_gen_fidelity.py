"""AUD-gen: distribution fidelity checks never pass on missing evidence (#183)."""

from __future__ import annotations

import math

import pytest

from shape.generation import quantile_fidelity

REF = {"q25": 1.0, "q50": 2.0, "q75": 3.0}


@pytest.mark.parametrize(
    "observed",
    [
        {"q25": 1.0, "q50": math.nan, "q75": math.nan},
        {"q25": math.nan, "q50": 2.0, "q75": 3.0},
        {"q25": 1.0, "q50": 2.0, "q75": math.inf},
    ],
)
def test_a_nan_or_infinite_observed_quantile_fails(observed):
    # 183: max() skipped a NaN that was not first, so this passed with total_variation 0.0.
    assert not quantile_fidelity(REF, observed).passed


def test_a_reference_without_quantiles_certifies_nothing():
    # 183: an empty reference passed with error 0.
    assert not quantile_fidelity({}, {"q50": 9.0}).passed


def test_matching_quantiles_still_pass():
    assert quantile_fidelity(REF, dict(REF)).passed


def _feed():
    from shape.generation.schema import GenSchema

    return GenSchema.from_dict(
        {
            "schema_version": 1,
            "model": {"name": "feed", "seed": 7},
            "tables": {
                "orders": {
                    "name": "orders",
                    "primary_key": ["order_id"],
                    "columns": {
                        "order_id": {
                            "name": "order_id",
                            "type": "integer",
                            "generator": {"strategy": "sequence"},
                        },
                        "status": {
                            "name": "status",
                            "type": "string",
                            "generator": {
                                "strategy": "weighted_enum",
                                "values": {"completed": 80, "cancelled": 20},
                            },
                        },
                    },
                }
            },
            "generation": {"scale": "s", "scales": {"s": {"orders": 50}}},
        }
    )


_ADD = {
    "kind": "add_column",
    "table": "orders",
    "column": "region",
    "start": 5,
    "definition": {
        "type": "string",
        "generator": {"strategy": "weighted_enum", "values": {"n": 1, "s": 1}},
    },
}
_NULLS = {"kind": "null_rate", "table": "orders", "column": "region", "start": 7, "to": 0.3}
_DROP = {"kind": "drop_column", "table": "orders", "column": "status", "start": 5}
_WINDOW = {
    "kind": "null_rate",
    "table": "orders",
    "column": "status",
    "start": 0,
    "end": 3,
    "to": 0.3,
}


@pytest.mark.parametrize(
    ("events", "day", "columns"),
    [
        ([_ADD, _NULLS], 0, ["order_id", "status"]),
        ([_ADD, _NULLS], 8, ["order_id", "status", "region"]),
        ([_DROP, _WINDOW], 6, ["order_id"]),
        ([_WINDOW, _DROP], 6, ["order_id"]),
    ],
)
def test_an_event_on_a_column_absent_that_day_has_no_effect(events, day, columns):
    # 184: _column() ran before the weight check, so a null_rate event on a column added later
    # (or dropped earlier) raised DriftPlanError: no column orders.region, in one order only.
    from shape.generation.drift_plan import DriftPlan

    schema = DriftPlan(events, days=10).schema_at(_feed(), day)
    assert list(schema.tables["orders"].columns) == columns
