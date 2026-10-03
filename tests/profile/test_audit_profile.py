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


# ---- #224: text holding a NaN word is text, as in the baseline ----------------------------


@pytest.mark.parametrize("values", [["1", "NaN"] * 15, ["1", "nan", "2"] * 10])
def test_text_with_a_nan_word_is_text(kernel, tmp_path, values):
    import pandas as pd
    import pyarrow.parquet as pq

    assert _col(pa.array(values))["dtype"] == "string"
    assert shape.profile(pd.DataFrame({"c": values})).to_dict()["columns"]["c"]["dtype"] == "string"
    path = tmp_path / "t.parquet"
    pq.write_table(pa.table({"c": pa.array(values)}), path)
    assert shape.profile(str(path)).to_dict()["columns"]["c"]["dtype"] == "string"


def test_text_infinity_in_a_file_still_fails_as_the_baseline_does(kernel, tmp_path):
    import pyarrow.parquet as pq

    path = tmp_path / "t.parquet"
    pq.write_table(pa.table({"c": pa.array(["1.5", "inf", "2.5"] * 10)}), path)
    with pytest.raises(ValueError, match="non-finite"):
        shape.profile(str(path))


# ---- #225: zoned instants that share a wall-clock time stay apart -------------------------


def _fold(n: int) -> pa.Array:
    utc = dt.UTC
    v = [
        dt.datetime(2021, 11, 7, 5, 30, tzinfo=utc),  # 01:30 EDT
        dt.datetime(2021, 11, 7, 6, 30, tzinfo=utc),  # 01:30 EST, an hour later
        dt.datetime(2021, 11, 7, 6, 10, tzinfo=utc),  # 01:10 EST
        dt.datetime(2021, 11, 7, 4, 50, tzinfo=utc),  # 00:50 EDT
    ][:n]
    return pa.array(v * (5 if n == 4 else 1), pa.timestamp("us", tz="America/New_York"))


def test_two_instants_in_the_repeated_hour_are_two_values(kernel):
    # the pinned baseline's profile of the same Parquet column
    c = _col(_fold(2))
    assert c["cardinality"] == 2 and c["is_unique"] is True
    assert c["min_value"] == ["timestamp", "2021-11-07 01:30:00-04:00"]
    assert c["max_value"] == ["timestamp", "2021-11-07 01:30:00-05:00"]
    assert c["value_counts_ext"] == {
        "2021-11-07 01:30:00-04:00": 0.5,
        "2021-11-07 01:30:00-05:00": 0.5,
    }


def test_the_repeated_hour_keeps_first_seen_order_and_wall_clock_hours(kernel):
    c = _col(_fold(4))
    assert c["cardinality"] == 4
    assert c["value_counts_ext_order"] == [
        "2021-11-07 01:30:00-04:00",
        "2021-11-07 01:30:00-05:00",
        "2021-11-07 01:10:00-05:00",
        "2021-11-07 00:50:00-04:00",
    ]
    assert c["min_value"] == ["timestamp", "2021-11-07 00:50:00-04:00"]
    assert c["max_value"] == ["timestamp", "2021-11-07 01:30:00-05:00"]
    assert c["hour_histogram"][0] == 0.25 and c["hour_histogram"][1] == 0.75  # wall clock


# ---- #228: pandas object columns of mixed types -------------------------------------------

# (values, dtype, cardinality, null_count, value_counts_ext): the pinned baseline's profile
_MIXED = [
    ([1, "x", 2.5, None] * 8, "string", 3, 8, {"1": 0.333333, "x": 0.333333, "2.5": 0.333333}),
    ([True, 1, 0] * 10, "boolean", 2, 0, {"True": 0.666667, "0": 0.333333}),
    ([1, "2"] * 15, "integer", 2, 0, {"1": 0.5, "2": 0.5}),
    (
        [1, 2.5, "a", "b", None, True] * 5,
        "string",
        4,
        5,
        {"1": 0.4, "2.5": 0.2, "a": 0.2, "b": 0.2},
    ),
    ([1, "x", float("nan"), None] * 8, "string", 2, 16, {"1": 0.5, "x": 0.5}),
    (
        [np.int64(3), "x", np.float64(1.5)] * 10,
        "string",
        3,
        0,
        {"3": 0.333333, "x": 0.333333, "1.5": 0.333333},
    ),
    (
        [2**64, 2**70, 1] * 10,
        "float",
        3,
        0,
        {"18446744073709551616": 0.333333, "1180591620717411303424": 0.333333, "1": 0.333333},
    ),
    (
        [-(2**64), 2**63, 5] * 10,
        "float",
        3,
        0,
        {"-18446744073709551616": 0.333333, "9223372036854775808": 0.333333, "5": 0.333333},
    ),
]


@pytest.mark.parametrize(("values", "dtype", "card", "nulls", "counts"), _MIXED)
def test_mixed_object_columns_profile_as_the_baseline_does(
    kernel, values, dtype, card, nulls, counts
):
    import pandas as pd

    df = pd.DataFrame({"c": pd.Series(values, dtype=object), "n": range(len(values))})
    c = shape.profile(df).to_dict()["columns"]["c"]
    assert (c["dtype"], c["cardinality"], c["null_count"]) == (dtype, card, nulls)
    assert c["value_counts_ext"] == counts


def test_an_object_column_of_types_that_cannot_mix_says_what_to_do():
    import pandas as pd

    df = pd.DataFrame({"c": pd.Series([dt.date(2020, 1, 1), "2020-01-02"] * 3, dtype=object)})
    with pytest.raises(ValueError, match=r"column 'c'.*astype\(str\)"):
        shape.profile(df)


# ---- #229: duplicate column names in a table or DataFrame ---------------------------------


def test_duplicate_column_names_in_a_table_or_dataframe_are_refused():
    import pandas as pd

    t = pa.Table.from_arrays([pa.array([1, 3]), pa.array([2, 4])], names=["a", "a"])
    df = pd.DataFrame([[1, 2], [3, 4]], columns=["a", "a"])
    for source in (t, df):
        with pytest.raises(ValueError, match=r"duplicate column names \['a'\].*rename"):
            shape.profile(source)


# ---- #236: integers wider than 64 bits are float, as in the baseline -----------------------


@pytest.mark.parametrize("values", [[2**64, 2**70, 1] * 10, [-(2**64), 2**63, 5] * 10])
def test_integers_wider_than_64_bits_are_float_as_in_the_baseline(kernel, tmp_path, values):
    path = tmp_path / "big.csv"
    path.write_text("a\n" + "\n".join(map(str, values)) + "\n")
    c = shape.profile(str(path)).to_dict()["columns"]["a"]
    assert c["dtype"] == "float"
    if min(values) > 0:  # (the baseline's CSV reader keeps the negative case's values as text)
        assert c["min_value"] == ["int", min(values)] and c["max_value"] == ["int", max(values)]


# ---- #269: zoned date text is refused with the column and the way out ---------------------


@pytest.mark.parametrize("text", ["2024-01-01T00:00:00Z", "2024-01-01 10:00+02:00"])
def test_zoned_date_text_is_refused_naming_the_column_and_the_fix(tmp_path, text):
    path = tmp_path / "z.csv"
    path.write_text(f"n,when\n1,{text}\n2,{text}\n")
    with pytest.raises(NotImplementedError, match=r"column 'when'.*to_datetime"):
        shape.profile(str(path))


# ---- #270: the date tokenizer is linear in the text's length ------------------------------


def test_a_long_text_value_is_tokenized_in_linear_time():
    import time

    from shape.profile.reference import dtparse

    start = time.perf_counter()
    assert dtparse.parse_mixed("1." * 200_000) is None  # 400 KB: 25 s when quadratic
    assert time.perf_counter() - start < 5.0


# ---- #271: a folder skips hidden and underscore folders, and refuses a nested Delta table ---


def test_a_folder_skips_hidden_and_underscore_folders(tmp_path):
    (tmp_path / "data.csv").write_text("a\n1\n2\n")
    (tmp_path / ".ipynb_checkpoints").mkdir()
    (tmp_path / ".ipynb_checkpoints" / "data-checkpoint.csv").write_text("a\n7\n")
    (tmp_path / "_temporary" / "0").mkdir(parents=True)
    (tmp_path / "_temporary" / "0" / "part-0.csv").write_text("a\n9\n")
    (tmp_path / "part" / "day=1").mkdir(parents=True)
    (tmp_path / "part" / "day=1" / "x.csv").write_text("a\n3\n")
    d = shape.profile(str(tmp_path)).to_dict()
    assert d["row_count"] == 3 and d["columns"]["a"]["max_value"] == ["int", 3]


def test_a_folder_holding_a_nested_delta_table_is_refused(tmp_path):
    deltalake = pytest.importorskip("deltalake")
    lake = tmp_path / "lake"
    deltalake.write_deltalake(str(lake / "sales"), pa.table({"v": [1, 2, 3]}))
    deltalake.write_deltalake(str(lake / "sales"), pa.table({"v": [100]}), mode="overwrite")
    with pytest.raises(ValueError, match=r"Delta table.*sales"):
        shape.profile(str(lake))
    assert shape.profile(str(lake / "sales")).to_dict()["row_count"] == 1


# ---- #272: an existing path with glob characters in its name is read as itself -------------


def test_an_existing_file_with_glob_characters_in_its_name_is_read(tmp_path):
    path = tmp_path / "x[1].csv"
    path.write_text("a\n1\n2\n")
    assert shape.profile(str(path)).to_dict()["row_count"] == 2
    (tmp_path / "y1.csv").write_text("a\n5\n")
    assert shape.profile(str(tmp_path / "y[0-9].csv")).to_dict()["row_count"] == 1  # still a glob
