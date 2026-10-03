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
    steps = [i % 50 for i in range(200)]  # repeated values: neither column is a unique key
    t = pa.table({"a": pa.array([2**53 + s * 7 for s in steps]), "x": [s * 2.0 for s in steps]})
    kinds = [a["kind"] for a in shape.profile(t).to_dict()["joint"]["associations"]]
    assert "numeric" in kinds


# ---- #167: duplicate and blank CSV header names are renamed as the baseline does ----------


@pytest.mark.parametrize(
    ("header", "names"),
    [
        ("a,a", ["a", "a.1"]),
        ("a,a.1,a", ["a", "a.1", "a.2"]),
        ("a,a,a.1", ["a", "a.2", "a.1"]),
        (",b", ["Unnamed: 0", "b"]),
        ("a,,", ["a", "Unnamed: 1", "Unnamed: 2"]),
        (",,Unnamed: 0", ["Unnamed: 0.1", "Unnamed: 1", "Unnamed: 0"]),
    ],
)
def test_csv_header_names_are_made_unique_as_the_baseline_does(tmp_path, header, names):
    width = header.count(",") + 1
    rows = "\n".join(",".join(str(r * width + i) for i in range(width)) for r in range(5))
    path = tmp_path / "h.csv"
    path.write_text(f"{header}\n{rows}\n")
    cols = shape.profile(str(path)).to_dict()["columns"]
    assert list(cols) == names
    for i, name in enumerate(names):  # each column keeps its own values
        assert cols[name]["min_value"] == ["int", i]


def test_duplicate_csv_header_with_signed_integers_profiles(tmp_path):
    path = tmp_path / "h.csv"
    path.write_text("a,a\n+1,+2\n+3,+4\n")
    cols = shape.profile(str(path)).to_dict()["columns"]
    assert cols["a"]["max_value"] == ["int", 3] and cols["a.1"]["max_value"] == ["int", 4]


# ---- #216: concurrent profile() calls keep their own columns ------------------------------


@pytest.mark.skipif(
    "fork" not in __import__("multiprocessing").get_all_start_methods(), reason="no fork pool"
)
def test_concurrent_profiles_on_the_fork_pool_keep_their_own_columns(monkeypatch):
    import threading

    import pandas as pd

    monkeypatch.setenv("PROFILE_POOL", "process")
    monkeypatch.setenv("PROFILE_THREADS", "2")
    r = np.random.default_rng(4)
    frames = {
        "A": pd.DataFrame({f"a{i}": r.integers(0, 10, 2000) for i in range(16)}),
        "B": pd.DataFrame({f"b{i}": r.integers(0, 10**5, 2000) for i in range(24)}),
    }
    for _ in range(5):
        out: dict[str, object] = {}

        def run(key: str, out: dict[str, object]) -> None:
            try:
                out[key] = sorted(shape.profile(frames[key]).to_dict()["columns"])
            except Exception as exc:  # noqa: BLE001 - a crash is a failure of this test
                out[key] = repr(exc)

        threads = [threading.Thread(target=run, args=(k, out)) for k in frames]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert out == {k: sorted(df.columns) for k, df in frames.items()}


# ---- #221: impossible ISO dates are text, not rolled into the next month ------------------


@pytest.mark.parametrize(
    ("values", "dtype"),
    [
        (["2023-02-29", "2023-03-01"], "string"),
        (["2023-02-28", "2023-02-29", "2023-03-01"], "string"),
        (["2024-06-31", "2024-07-01"], "string"),
        (["2016-12-31 23:59:60", "2017-01-01 00:00:00"], "string"),
        (["2023-01-01 10:60:00", "2023-01-01 10:00:00"], "string"),
        (["2024-02-29", "2023-03-01"], "datetime"),  # a real leap day
        (["2023-01-01T10:00:59", "2023-01-01T23:59:00"], "datetime"),
    ],
)
def test_impossible_iso_dates_are_text_as_in_the_baseline(kernel, tmp_path, values, dtype):
    path = tmp_path / "d.csv"
    path.write_text("d\n" + "\n".join(values) + "\n")
    assert shape.profile(str(path)).to_dict()["columns"]["d"]["dtype"] == dtype


@pytest.mark.parametrize("bad", ["2023-02-29", "2023-02-29 10:00:00"])
def test_an_impossible_date_among_many_repeats_is_found_from_the_distinct_values(tmp_path, bad):
    good = "2023-03-01" if len(bad) == 10 else "2023-03-01 10:00:00"
    path = tmp_path / "d.csv"
    path.write_text("d\n" + "\n".join([good] * 40 + [bad] + [good] * 40) + "\n")
    assert shape.profile(str(path)).to_dict()["columns"]["d"]["dtype"] == "string"
    path.write_text("d\n" + "\n".join([good] * 81) + "\n")
    assert shape.profile(str(path)).to_dict()["columns"]["d"]["dtype"] == "datetime"
