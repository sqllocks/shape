"""Issue #219: a seasonal ``temporal`` range spreads the probability of the (month, weekday)
buckets it has no day in over every day of the range, the end day included; a profile that is
not a mapping of names to weights is a ``StrategyError`` naming the column."""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

import numpy as np
import pytest

from shape.builtins.strategies.temporal import _day_weights
from shape.generation.engine import Engine
from shape.generation.schema import GenSchema
from shape.generation.strategy_kit import StrategyError


def _days(spec: dict[str, Any], n: int) -> list[date]:
    doc = {
        "schema_version": 1,
        "model": {"name": "t", "seed": 7},
        "tables": {
            "t": {
                "name": "t",
                "primary_key": ["id"],
                "columns": {
                    "id": {"name": "id", "type": "integer", "generator": {"strategy": "sequence"}},
                    "ts": {
                        "name": "ts",
                        "type": "datetime",
                        "generator": {"strategy": "temporal", **spec},
                    },
                },
            }
        },
        "relationships": [],
        "generation": {"scale": "s", "scales": {"s": {"t": n}}},
    }
    values = Engine(GenSchema.from_dict(doc), seed=7).generate().tables["t"]["ts"].to_pylist()
    return [v.date() for v in values]


def test_empty_bucket_mass_reaches_the_end_day() -> None:
    # January 2024 only; the profile puts most weight on February, which has no day here.
    days = Counter(
        _days(
            {
                "pattern": "seasonal",
                "start": "2024-01-01",
                "end": "2024-01-31",
                "profiles": {"month": {"Feb": 11}},
            },
            31_000,
        )
    )
    assert len(days) == 31
    expected = 31_000 / 31
    for day in (1, 2, 15, 30, 31):
        # 1,000 draws per day: 5 sigma is about 155.
        assert abs(days[date(2024, 1, day)] - expected) < 160, (day, days[date(2024, 1, day)])


def test_day_weights_spread_empty_mass_over_every_day() -> None:
    month = np.full(12, 1.0 / 12)
    dow = np.full(7, 1.0 / 7)
    first = int(np.datetime64("2024-01-01", "D").astype(np.int64))
    w = _day_weights(first, 31, month, dow)
    assert w.sum() == pytest.approx(1.0)
    # 2024-01-31 and 2024-01-24 are both one of January's five Wednesdays: the same weight.
    assert w[30] == pytest.approx(w[23])
    # Every day gets the same share of the eleven empty months' mass (11/12 over 31 days).
    shares = w - np.where(np.isin(np.arange(31) % 7, [0, 1, 2]), 1 / 84 / 5, 1 / 84 / 4)
    assert np.allclose(shares, 11 / 12 / 31)


def test_a_one_day_range_keeps_all_the_mass() -> None:
    month = np.array([0.0, 1.0] + [0.0] * 10)
    dow = np.full(7, 1.0 / 7)
    first = int(np.datetime64("2024-01-10", "D").astype(np.int64))
    w = _day_weights(first, 1, month, dow)
    assert w.tolist() == [1.0]


@pytest.mark.parametrize(
    "profiles",
    [
        {"month": [10, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]},
        {"day_of_week": [1, 2, 3, 4, 5, 6, 7]},
        {"hour_of_day": [1] * 24},
        {"month": "Jan"},
        {"month": {"Jan": "heavy"}},
    ],
    ids=["month-list", "dow-list", "hour-list", "month-text", "weight-text"],
)
def test_a_malformed_profile_is_a_strategy_error_naming_the_column(
    profiles: dict[str, Any],
) -> None:
    spec = {"pattern": "seasonal", "start": "2024-01-01", "end": "2024-12-31"}
    with pytest.raises(StrategyError, match=r"t\.ts"):
        _days({**spec, "profiles": profiles}, 10)


def test_top_level_weights_are_checked_too() -> None:
    spec = {"pattern": "seasonal", "start": "2024-01-01", "end": "2024-12-31"}
    with pytest.raises(StrategyError, match=r"t\.ts"):
        _days({**spec, "month_weights": [1] * 12}, 10)
