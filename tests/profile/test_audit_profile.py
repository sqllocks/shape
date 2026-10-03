"""Profiling engine audit (lane AUD-profile): each defect has a test that failed before its fix."""

from __future__ import annotations

import datetime as dt

import pyarrow as pa
import pytest

import shape
from shape.kernel import dispatch


@pytest.fixture(params=["python", "rust"])
def kernel(request, monkeypatch):
    if request.param == "rust":
        pytest.importorskip("shape._kernel")
    monkeypatch.setenv("SHAPE_KERNEL", request.param)
    dispatch.reset()
    yield request.param
    dispatch.reset()


def _col(arr: pa.Array) -> dict:
    return shape.profile(pa.table({"c": arr})).to_dict()["columns"]["c"]


# ---- zoned timestamps in every unit ---------------------------------------------------------


@pytest.mark.parametrize("unit", ["s", "ms", "us", "ns"])
@pytest.mark.parametrize("tz", ["America/New_York", "+05:30"])
def test_zoned_timestamps_profile_the_same_in_every_unit(kernel, unit, tz):
    values = [dt.datetime(2020, 1, 1), dt.datetime(2021, 6, 1, 12, 30)] * 20
    want = _col(pa.array(values, pa.timestamp("us", tz=tz)))
    got = _col(pa.array(values, pa.timestamp(unit, tz=tz)))
    assert got["min_value"] == want["min_value"]
    assert got["max_value"] == want["max_value"]
    assert got["value_counts_ext"] == want["value_counts_ext"]
