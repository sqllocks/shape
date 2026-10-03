"""Profiling engine audit (lane AUD-profile): each defect has a test that failed before its fix."""

from __future__ import annotations

import datetime as dt

import numpy as np
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


# ---- #150: the joint sample never repeats a row --------------------------------------------


@pytest.mark.parametrize("rows", [20_001, 20_500, 25_001, 100_001, 1_000_003])
def test_the_joint_sample_has_no_repeated_rows(rows):
    from shape.profile.joint.analyze import _sample_index, budget_for

    ix = _sample_index(rows, budget_for(rows))
    assert ix is not None
    assert len(np.unique(ix)) == len(ix) == budget_for(rows).sample_rows
    assert ix.min() >= 0 and ix.max() < rows and (np.diff(ix) > 0).all()


def test_a_unique_column_just_over_the_sample_budget_is_not_a_determinant(kernel):
    rng = np.random.default_rng(1)
    n = 20_001
    t = pa.table(
        {
            "id": [f"ID{i:06d}" for i in range(n)],
            "color": rng.choice(["r", "g", "b"], n),
            "order_no": [f"O{i}" for i in range(n)],
        }
    )
    joint = shape.profile(t).to_dict()["joint"]
    assert joint["sampled"] is True
    assert joint["dependencies"] == []


# ---- #151: nanosecond timestamps and integers past 2**53 keep their numeric view ----------


def test_nanosecond_timestamps_are_in_the_joint_analysis(kernel):
    base = 1_577_836_800_000_000_000  # 2020-01-01 in ns
    hours = [base + i * 3_600_000_000_000 for i in range(200)]
    x = np.arange(200) * 2.0
    out = {}
    for unit, div in (("ns", 1), ("us", 1000)):
        t = pa.table({"t": pa.array([h // div for h in hours], pa.timestamp(unit)), "x": x})
        out[unit] = shape.profile(t).to_dict().get("joint")
    assert out["ns"] is not None
    assert out["ns"]["associations"] == out["us"]["associations"]


def test_integers_past_two_to_the_53_keep_their_numeric_associations(kernel):
    t = pa.table({"a": pa.array([2**53 + i * 7 for i in range(200)]), "x": np.arange(200) * 2.0})
    kinds = [a["kind"] for a in shape.profile(t).to_dict()["joint"]["associations"]]
    assert "numeric" in kinds
