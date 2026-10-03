"""Regression tests of the AUD-builtins audit (issues #129 to #149): the built-in strategies,
distribution families and calendars."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pyarrow as pa

from shape.generation.engine import Engine
from shape.generation.schema import GenSchema


def _table(
    columns: dict[str, dict[str, Any]],
    rows: int = 1000,
    seed: int = 7,
    chunk_rows: int | None = None,
) -> pa.Table:
    doc = {
        "schema_version": 1,
        "model": {"name": "t", "seed": seed},
        "tables": {
            "t": {"name": "t", "columns": {k: {"name": k, **v} for k, v in columns.items()}}
        },
        "relationships": [],
        "generation": {"scale": "s", "scales": {"s": {"t": rows}}},
    }
    options = {} if chunk_rows is None else {"chunk_rows": chunk_rows}
    return Engine(GenSchema.from_dict(doc), seed=seed, **options).generate().tables["t"]


def _times(
    generator: dict[str, Any], rows: int = 2000, chunk_rows: int | None = None
) -> list[datetime]:
    table = _table(
        {"ts": {"type": "datetime", "generator": {"strategy": "temporal", **generator}}},
        rows,
        chunk_rows=chunk_rows,
    )
    return list(table["ts"].to_pylist())


# ---- #129: seasonal temporal values stay inside a start and end with a time of day ----------


def test_seasonal_values_stay_inside_timed_bounds() -> None:
    start, end = datetime(2024, 1, 1, 12), datetime(2024, 1, 3, 6)
    bounds = {"pattern": "seasonal", "start": start.isoformat(), "end": end.isoformat()}
    for profiles in (
        {"month": {"Jan": 1}},
        {"day_of_week": {"Mon": 3, "Tue": 1, "Wed": 1}},
        {"hour_of_day": {"10": 1, "20": 1}},
        {"month": {"Jan": 1}, "hour_of_day": {"3": 1, "13": 1}},
    ):
        values = _times({**bounds, "profiles": profiles})
        assert min(values) >= start and max(values) < end, profiles


def test_seasonal_exclusive_midnight_end_is_not_a_possible_day() -> None:
    values = _times(
        {
            "pattern": "seasonal",
            "start": "2024-01-01",
            "end": "2024-01-03T00:00:00",
            "profiles": {"month": {"Jan": 1}},
        }
    )
    assert max(values) < datetime(2024, 1, 3)
    assert {v.day for v in values} == {1, 2}


def test_seasonal_timed_bounds_do_not_depend_on_chunking() -> None:
    spec = {
        "pattern": "seasonal",
        "start": "2024-01-01T12:00:00",
        "end": "2024-01-09T06:30:00",
        "profiles": {"day_of_week": {"Mon": 2}, "hour_of_day": {"9": 1, "17": 2}},
    }
    assert _times(spec, 700) == _times(spec, 700, chunk_rows=97)


def test_seasonal_partial_edge_days_weigh_their_share_of_the_day() -> None:
    # 18:00 on the 1st to 06:00 on the 3rd with every day and hour equally likely: the 1st and
    # the 3rd hold a quarter of a day each and the 2nd a whole day, so about two thirds of the
    # values fall on the 2nd
    values = _times(
        {
            "pattern": "seasonal",
            "start": "2024-01-01T18:00:00",
            "end": "2024-01-03T06:00:00",
            "profiles": {"hour_of_day": {str(h): 1 for h in range(24)}},
        },
        rows=6000,
    )
    share = sum(v.day == 2 for v in values) / len(values)
    assert abs(share - 2 / 3) < 0.03
