"""``temporal``: a ``pattern`` other than ``uniform`` or ``seasonal`` is a ``StrategyError`` naming
the column (it was silently uniform), and a seasonal ``calendar`` multiplies each day's weight, so
a factor of 0 closes the day (``docs/GENERATION_STRATEGIES.md``)."""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import Any

import pytest

from shape.generation.engine import Engine
from shape.generation.schema import GenSchema
from shape.generation.strategy_kit import StrategyError

WEEKDAYS = {"Mon": 1, "Tue": 1, "Wed": 1, "Thu": 1, "Fri": 1, "Sat": 0, "Sun": 0}


def _schema(spec: dict[str, Any], n: int) -> GenSchema:
    return GenSchema.from_dict(
        {
            "schema_version": 1,
            "model": {"name": "t", "seed": 7},
            "tables": {
                "t": {
                    "name": "t",
                    "primary_key": ["id"],
                    "columns": {
                        "id": {
                            "name": "id",
                            "type": "integer",
                            "generator": {"strategy": "sequence"},
                        },
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
    )


def _days(spec: dict[str, Any], n: int) -> list[date]:
    values = Engine(_schema(spec, n), seed=7).generate().tables["t"]["ts"].to_pylist()
    return [v.date() for v in values]


@pytest.mark.parametrize("pattern", ["trading_days", "Seasonal", "", "weekly"])
def test_an_unknown_pattern_is_an_error_naming_the_column(pattern: str) -> None:
    with pytest.raises(StrategyError, match=r"pattern.*uniform, seasonal.*\(t\.ts\)"):
        _days({"pattern": pattern}, 10)


@pytest.mark.parametrize(
    "spec", [{}, {"pattern": None}, {"pattern": "uniform"}, {"pattern": "seasonal"}]
)
def test_the_known_patterns_still_generate(spec: dict[str, Any]) -> None:
    assert len(_days(spec, 10)) == 10


def test_calendar_is_a_known_temporal_key() -> None:
    spec = {"pattern": "seasonal", "calendar": {"events": []}}
    issues = [i.message for i in _schema(spec, 1).validate()]
    assert not [m for m in issues if "calendar" in m], issues


def test_a_calendar_factor_of_zero_closes_the_day() -> None:
    closed = [date(2024, 3, 4), date(2024, 3, 15)]
    spec = {
        "pattern": "seasonal",
        "start": "2024-03-01",
        "end": "2024-03-31",
        "calendar": {
            "events": [
                {"name": "a", "lift": 0, "date": closed[0].isoformat()},
                {"name": "b", "lift": 0, "rule": {"month": 3, "day": 15}},
            ]
        },
    }
    days = Counter(_days(spec, 29_000))
    assert not set(closed) & set(days)
    assert len(days) == 29  # every other day of March is drawn
    for day, count in days.items():
        assert 850 < count < 1150, (day, count)  # 1,000 each


def test_a_calendar_lift_weighs_its_day() -> None:
    spec = {
        "pattern": "seasonal",
        "start": "2024-03-01",
        "end": "2024-03-10",
        "calendar": {"events": [{"name": "sale", "lift": 3, "date": "2024-03-05"}]},
    }
    days = Counter(_days(spec, 12_000))
    assert 2_700 < days[date(2024, 3, 5)] < 3_300  # 3 of 12 shares
    assert 850 < days[date(2024, 3, 6)] < 1_150


def test_weekend_weights_and_a_calendar_make_business_days() -> None:
    spec = {
        "pattern": "seasonal",
        "start": "2022-01-01",
        "end": "2025-12-31",
        "profiles": {"day_of_week": WEEKDAYS},
        "calendar": {"events": [{"name": "x", "lift": 0, "rule": {"month": 7, "day": 4}}]},
        "granularity": "day",
    }
    days = _days(spec, 20_000)
    assert all(d.weekday() < 5 for d in days)
    assert not [d for d in days if (d.month, d.day) == (7, 4)]


def test_the_calendar_is_deterministic_and_row_addressed() -> None:
    spec = {
        "pattern": "seasonal",
        "profiles": {"day_of_week": WEEKDAYS},
        "calendar": {"events": [{"name": "gf", "lift": 0, "rule": {"easter": -2}}]},
    }
    schema = _schema(spec, 5_000)
    whole = Engine(schema, seed=3).generate().tables["t"]["ts"]
    assert whole.equals(Engine(schema, seed=3).generate().tables["t"]["ts"])
    part = Engine(schema, seed=3).generate_column("t", "ts", 1_234, 777)
    assert part.equals(whole.slice(1_234, 777).combine_chunks())


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ({"calendar": {"events": []}}, "needs pattern 'seasonal'"),
        ({"pattern": "uniform", "calendar": {"events": []}}, "needs pattern 'seasonal'"),
        ({"pattern": "seasonal", "calendar": "us_federal"}, "calendar spec mapping"),
        ({"pattern": "seasonal", "calendar": {"calendars": ["nope"]}}, "not valid"),
        (
            {
                "pattern": "seasonal",
                "calendar": {"events": [{"name": "x", "lift": -1, "date": "2024-01-01"}]},
            },
            "not valid",
        ),
    ],
)
def test_calendar_errors_name_the_column(spec: dict[str, Any], message: str) -> None:
    with pytest.raises(StrategyError, match=rf"{message}.*\(t\.ts\)"):
        _days({"start": "2024-01-01", "end": "2024-01-31", **spec}, 10)


def test_a_calendar_that_closes_every_day_is_an_error() -> None:
    start = date(2024, 1, 1)
    closed = [(start + timedelta(days=i)).isoformat() for i in range(3)]
    spec = {
        "pattern": "seasonal",
        "start": "2024-01-01",
        "end": "2024-01-03",
        "calendar": {"events": [{"name": "shut", "lift": 0, "dates": closed}]},
    }
    with pytest.raises(StrategyError, match=r"at least one open day.*\(t\.ts\)"):
        _days(spec, 10)
