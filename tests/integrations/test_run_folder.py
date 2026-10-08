"""PF-06b: two runs in the same second never share an artifact folder."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from shape.integrations.fabric.run_folder import (
    claim_run_folder,
    parse_run_folder,
    run_stamp,
    unique_run_name,
)

SECOND = datetime(2026, 9, 30, 12, 0, 0, 123456, tzinfo=UTC)


def test_the_name_carries_microseconds_and_parses_back():
    assert run_stamp(SECOND) == "20260930T120000123456Z"
    assert parse_run_folder("20260930T120000123456Z") == SECOND
    assert parse_run_folder("20260930T120000123456Z_3") == SECOND


def test_the_one_second_names_of_earlier_runs_still_parse_and_sort_between_seconds():
    assert parse_run_folder("20260930T120000Z") == SECOND.replace(microsecond=0)
    assert parse_run_folder("latest") is None and parse_run_folder("contract.json") is None
    old, same, nxt = "20260930T120000Z", run_stamp(SECOND), "20260930T120001000000Z"
    assert same < old < nxt


def test_runs_in_the_same_instant_get_distinct_folders_and_keep_both_baselines(tmp_path: Path):
    parent = tmp_path / "shape" / "orders"
    first = claim_run_folder(parent, now=SECOND)
    (first / "orders.shape").write_text("baseline", encoding="utf-8")
    second = claim_run_folder(parent, now=SECOND)
    third = claim_run_folder(parent, now=SECOND)
    assert len({first, second, third}) == 3
    assert (first / "orders.shape").read_text(encoding="utf-8") == "baseline"
    assert [p.name for p in sorted(parent.iterdir())] == [
        "20260930T120000123456Z",
        "20260930T120000123456Z_2",
        "20260930T120000123456Z_3",
    ]
    assert {parse_run_folder(p.name) for p in parent.iterdir()} == {SECOND}


def test_two_real_runs_back_to_back_do_not_share_a_folder(tmp_path: Path):
    names = {claim_run_folder(tmp_path / "x").name for _ in range(50)}
    assert len(names) == 50


def test_unique_run_name_skips_taken_names():
    taken = {"s/orders/20260930T120000123456Z", "s/orders/20260930T120000123456Z_2"}
    assert unique_run_name("s/orders/", taken.__contains__, now=SECOND) == (
        "20260930T120000123456Z_3"
    )
    assert unique_run_name("s/orders", set().__contains__, now=SECOND) == "20260930T120000123456Z"


@pytest.mark.parametrize(
    "name", ["20261340T000000Z", "20260230T000000Z", "20260930T250000Z", "20260930T126000Z_2"]
)
def test_an_impossible_date_is_not_a_run_folder(name):
    """#379: a name of the right shape but an impossible date or time returns None."""
    assert parse_run_folder(name) is None
