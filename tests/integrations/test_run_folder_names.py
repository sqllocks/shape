"""``parse_run_folder`` returns None for every name that is not a run folder (HUNT2-fabric #638)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from shape.integrations.fabric.run_folder import parse_run_folder


@pytest.mark.parametrize(
    "name",
    [
        "20261399T999999Z",  # month 13
        "20260230T120000Z",  # 30 February
        "20260930T240000Z",  # hour 24
        "20260930T126000Z",  # minute 60
        "٢٠٢٦٠٩٣٠T120000Z",  # Arabic-Indic digits
        "20260930T120000123456Z_",
        "",
        "latest",
    ],
)
def test_a_name_that_is_not_a_run_folder_gives_none(name):
    assert parse_run_folder(name) is None


def test_the_current_and_earlier_names_still_parse():
    assert parse_run_folder("20260930T120000123456Z") == datetime(
        2026, 9, 30, 12, 0, 0, 123456, tzinfo=UTC
    )
    assert parse_run_folder("20260930T120000Z") == datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)
    assert parse_run_folder("20260930T120000123456Z_3") == datetime(
        2026, 9, 30, 12, 0, 0, 123456, tzinfo=UTC
    )
