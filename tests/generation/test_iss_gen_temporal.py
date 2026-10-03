"""ISS-gen #10: a ``temporal`` ``end`` date means the same for every pattern: the end day is a
possible day, so ``start == end`` is one single day (a business day's landing file)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pytest

from shape.generation.engine import Engine
from shape.generation.schema import GenSchema


def _times(spec: dict[str, Any], n: int = 400) -> list[datetime]:
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
    return Engine(GenSchema.from_dict(doc), seed=7).generate().tables["t"]["ts"].to_pylist()


PROFILES = {"month": {"Jan": 1}, "day_of_week": {"Mon": 1, "Fri": 1}}


@pytest.mark.parametrize(
    "extra",
    [
        {"pattern": "uniform"},
        {"pattern": "seasonal"},
        {"pattern": "seasonal", "profiles": {"hour_of_day": {"9": 1, "10": 1}}},
        {"pattern": "seasonal", "profiles": {"month": {"May": 1}}},
    ],
    ids=["uniform", "seasonal", "seasonal-hours", "seasonal-month"],
)
def test_one_day_range_gives_that_day(extra: dict[str, Any]) -> None:
    values = _times({"start": "2026-05-01", "end": "2026-05-01", **extra})
    assert {v.date() for v in values} == {date(2026, 5, 1)}


@pytest.mark.parametrize("pattern", ["uniform", "seasonal"])
def test_the_end_day_is_a_possible_day_for_both_patterns(pattern: str) -> None:
    values = _times({"pattern": pattern, "start": "2026-05-01", "end": "2026-05-02"})
    days = {v.date() for v in values}
    assert days == {date(2026, 5, 1), date(2026, 5, 2)}


def test_a_datetime_end_stays_the_exact_exclusive_bound() -> None:
    values = _times({"start": "2026-05-01T00:00:00", "end": "2026-05-01T12:00:00"})
    assert all(v < datetime(2026, 5, 1, 12) for v in values)


def test_the_errors_say_which_case_it_is() -> None:
    with pytest.raises(ValueError, match=r"end 2026-04-30 is before the start 2026-05-01"):
        _times({"start": "2026-05-01", "end": "2026-04-30"})
    with pytest.raises(ValueError, match=r"end equals the start.*date"):
        _times({"start": "2026-05-01T08:00:00", "end": "2026-05-01T08:00:00"})
