"""landing_zone takes a real date in ASCII digits and an hour 0 to 23 (#454)."""

from __future__ import annotations

from typing import Any

import pytest
from shape_fabric import onelake

from shape.errors import ShapeError


@pytest.mark.parametrize("dt", ["2024-13-45", "2024-02-30", "２０２４-０１-０１", "2024-1-1", "x"])
def test_a_date_that_is_not_a_calendar_date_is_refused(dt: str) -> None:
    with pytest.raises(ShapeError, match="date"):
        onelake.landing_zone("/b", "d", "e", dt)


@pytest.mark.parametrize("hour", [1.9, True, "abc", "1.5", -1, 24, "２"])
def test_an_hour_that_is_not_a_whole_hour_is_refused(hour: Any) -> None:
    with pytest.raises(ShapeError, match="hour"):
        onelake.landing_zone("/b", "d", "e", "2024-01-01", hour)


@pytest.mark.parametrize(("hour", "folder"), [(0, "hour=00"), ("7", "hour=07"), (23, "hour=23")])
def test_valid_hours(hour: Any, folder: str) -> None:
    out = onelake.landing_zone("/b", "d", "e", "2024-02-29", hour)
    assert out == f"/b/landing/d/e/dt=2024-02-29/{folder}"
