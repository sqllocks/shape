"""Profiling engine audit (lane AUD-profile): each defect has a test that failed before its fix."""

from __future__ import annotations

import datetime as dt
import math

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


# ---- #302: reference pairs with NaN, infinity, a Path, a missing file ---------------------


def _zip_data() -> pa.Table:
    zips = pa.array([2872.0, float("nan"), 10001.0, float("inf"), 99.0] * 4)
    return pa.table({"zip": zips, "city": ["a", "b", "c", "d", "e"] * 4})


_ZIP_REF = pa.table({"zip": ["02872", "10001"], "city": ["a", "c"]})


def test_reference_pairs_skip_nan_and_compare_infinity_as_text():
    spec = [{"columns": ["zip", "city"], "reference": _ZIP_REF}]
    (pair,) = shape.profile(_zip_data(), reference_pairs=spec).to_dict()["joint"]["reference_pairs"]
    assert pair["rows"] == 16  # the NaN rows are missing values
    assert pair["mismatched"] == 8 and pair["match_rate"] == 0.5


def test_reference_pairs_read_a_path_and_say_when_the_file_is_missing(tmp_path):
    import pyarrow.csv as pacsv

    ref = tmp_path / "zips.csv"
    pacsv.write_csv(_ZIP_REF, ref)
    spec = [{"columns": ["zip", "city"], "reference": ref}]
    (pair,) = shape.profile(_zip_data(), reference_pairs=spec).to_dict()["joint"]["reference_pairs"]
    assert pair["rows"] == 16
    missing = [{"columns": ["zip", "city"], "reference": str(tmp_path / "nope.csv")}]
    with pytest.raises(FileNotFoundError, match="nope.csv"):
        shape.profile(_zip_data(), reference_pairs=missing)


# ---- #313: NaN in the joint analysis --------------------------------------------------------


def test_nan_is_not_a_joint_placeholder(kernel):
    r = np.random.default_rng(0)
    score = r.choice([1.5, 2.5, 3.5], 2000)
    score[r.random(2000) < 0.105] = np.nan
    t = pa.table({"score": score, "grade": r.choice(["a", "b", "c"], 2000)})
    assert shape.profile(t).to_dict()["joint"]["implausible_by_placeholder"] == 0.0


def test_an_all_infinite_column_profiles_without_a_warning(kernel):
    import warnings

    t = pa.table({"x": [float("inf"), float("-inf")] * 10, "y": [float(i % 3) for i in range(20)]})
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        shape.profile(t)


# ---- #315: the row evidence functions skip NaN and read numpy numbers ---------------------


def test_pearson_skips_nan_and_reads_numpy_numbers():
    from shape.profile import pearson

    line = [{"a": float(i), "b": 2.0 * i + 1} for i in range(10)]
    assert pearson(
        [*line, {"a": float("nan"), "b": 1.0}, {"a": 1.0, "b": float("inf")}], "a", "b"
    ) == pytest.approx(1.0)
    numpy_rows = [{"a": np.int64(i), "b": np.float32(2 * i)} for i in range(10)]
    assert pearson(numpy_rows, "a", "b") == pytest.approx(1.0)


def test_conditional_means_and_mutual_information_skip_nan():
    from shape.profile import conditional_numeric_means, normalized_mutual_information

    rows = [{"c": "x", "v": float("nan")}] * 30 + [{"c": "x", "v": 1.0}] * 20
    assert conditional_numeric_means(rows, "c", "v") == {"x": 1.0}
    clean = [{"a": float(i), "b": float(i % 3)} for i in range(50)]
    with_nan = [*clean, {"a": float("nan"), "b": 1.0}]
    assert normalized_mutual_information(with_nan, "a", "b") == normalized_mutual_information(
        clean, "a", "b"
    )
    numpy_rows = [{"a": np.float64(r["a"]), "b": r["b"]} for r in clean]
    assert normalized_mutual_information(numpy_rows, "a", "b") == normalized_mutual_information(
        clean, "a", "b"
    )


# ---- #316: temporal profiles of nanosecond and empty columns -------------------------------


def test_temporal_profile_reads_nanoseconds_and_refuses_an_empty_column_clearly():
    from shape.profile.temporal import holiday_lifts, temporal_profile

    ns = temporal_profile(
        pa.array([1_000_000_001_500, 1_000_003_600_000_000_999], pa.timestamp("ns"))
    )
    us = temporal_profile(pa.array([1_000_000_001, 1_000_003_600_000_000], pa.timestamp("us")))
    assert ns == us
    with pytest.raises(ValueError, match="at least one non-null value"):
        holiday_lifts(pa.array([], pa.date32()), {})


# ---- #317: every stored minimum and maximum is cut to 256 characters ----------------------


def test_binary_min_and_max_are_cut_like_text(kernel):
    c = _col(pa.array([b"x" * 10_000, b"y" * 10_000], pa.binary()))
    for key in ("min_value", "max_value"):
        assert len(c[key][1]) == 257 and c[key][1].endswith("…")


# ---- #318: nanosecond timestamps keep their nanoseconds ------------------------------------


def test_nanosecond_timestamps_keep_their_nanoseconds(kernel):
    # the pinned baseline's profile of the same Parquet columns
    c = _col(
        pa.array(
            [1, 2, 2, 1_000_000_000_123_456_789] * 1 + [1_000_000_000_123_456_789],
            pa.timestamp("ns"),
        )
    )
    assert c["min_value"] == ["timestamp", "1970-01-01 00:00:00.000000001"]
    assert c["max_value"] == ["timestamp", "2001-09-09 01:46:40.123456789"]
    assert c["value_counts_ext"] == {
        "1970-01-01 00:00:00.000000002": 0.4,
        "2001-09-09 01:46:40.123456789": 0.4,
        "1970-01-01 00:00:00.000000001": 0.2,
    }
    z = _col(pa.array([1500, 2500, 2500, 1_000_000_000_123_456_789], pa.timestamp("ns", tz="UTC")))
    assert z["min_value"] == ["timestamp", "1970-01-01 00:00:00.000001500+00:00"]
    assert z["max_value"] == ["timestamp", "2001-09-09 01:46:40.123456789+00:00"]
    assert z["value_counts_ext"] == {
        "1970-01-01 00:00:00.000002500+00:00": 0.5,
        "1970-01-01 00:00:00.000001500+00:00": 0.25,
        "2001-09-09 01:46:40.123456789+00:00": 0.25,
    }


@pytest.mark.skipif(
    "fork" not in __import__("multiprocessing").get_all_start_methods(), reason="no fork pool"
)
def test_nanoseconds_survive_the_fork_pool(monkeypatch):
    monkeypatch.setenv("PROFILE_POOL", "process")
    monkeypatch.setenv("PROFILE_THREADS", "2")
    cols = {f"c{i}": [1, 2, 3, 4] for i in range(8)}
    cols["t"] = pa.array([1, 2, 2, 5], pa.timestamp("ns"))
    c = shape.profile(pa.table(cols)).to_dict()["columns"]["t"]
    assert c["min_value"] == ["timestamp", "1970-01-01 00:00:00.000000001"]
    assert c["max_value"] == ["timestamp", "1970-01-01 00:00:00.000000005"]


# ---- #319: a workbook source refuses the options it cannot use, and measures reference pairs --


def _workbook(tmp_path) -> str:
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(["a", "b"])
    for i in range(12):
        ws.append([f"x{i % 3}", i])
    path = tmp_path / "b.xlsx"
    wb.save(path)
    return str(path)


@pytest.mark.parametrize(
    "option",
    [
        {"version": 1},
        {"as_of": "garbage"},
        {"delimiter": ";"},
        {"encoding": "latin-1"},
        {"quotechar": "'"},
        {"header": False},
    ],
)
def test_a_workbook_refuses_the_options_it_cannot_use(tmp_path, option):
    (name,) = option
    with pytest.raises(ValueError, match=rf"\b{name}\b.*workbook"):
        shape.profile(_workbook(tmp_path) + "#Data", **option)


def test_a_workbook_sheet_measures_reference_pairs(tmp_path):
    ref = pa.table({"a": ["x0", "x1"], "b": [0, 1]})
    spec = [{"columns": ["a", "b"], "reference": ref}]
    (pair,) = shape.profile(_workbook(tmp_path) + "#Data", reference_pairs=spec).to_dict()["joint"][
        "reference_pairs"
    ]
    assert pair["rows"] == 12 and pair["mismatched"] == 10
    with pytest.raises(FileNotFoundError, match="nonexistent"):
        shape.profile(
            _workbook(tmp_path) + "#Data",
            reference_pairs=[{"columns": ["a", "b"], "reference": "/nonexistent.csv"}],
        )
    whole = shape.profile(_workbook(tmp_path), reference_pairs={"Data": spec}).to_dict()
    assert whole["tables"]["Data"]["joint"]["reference_pairs"][0]["mismatched"] == 10


# ---- #320: CSV files the baseline reads ---------------------------------------------------


def test_a_trailing_delimiter_makes_the_first_field_the_index_as_in_the_baseline(kernel, tmp_path):
    path = tmp_path / "t.csv"
    path.write_text("a,b\n1,2,\n3,4,\n")
    d = shape.profile(str(path)).to_dict()
    assert d["row_count"] == 2 and list(d["columns"]) == ["a", "b"]
    assert d["columns"]["a"]["dtype"] == "integer"
    assert d["columns"]["a"]["min_value"] == ["int", 2] and d["columns"]["a"]["max_value"] == [
        "int",
        4,
    ]
    assert d["columns"]["b"]["null_count"] == 2


def test_a_row_with_an_extra_field_later_in_the_file_is_still_refused(tmp_path):
    path = tmp_path / "t.csv"
    path.write_text("a,b\n1,2\n3,4,5\n")
    with pytest.raises(pa.ArrowInvalid, match="Expected 2 columns, got 3"):
        shape.profile(str(path))


@pytest.mark.parametrize("text", ["a,b", "a,b\n", "a,b\r\n"])
def test_a_header_only_csv_has_text_columns_and_no_rows(kernel, tmp_path, text):
    path = tmp_path / "t.csv"
    path.write_bytes(text.encode())
    d = shape.profile(str(path)).to_dict()
    assert d["row_count"] == 0 and list(d["columns"]) == ["a", "b"]
    assert {c["dtype"] for c in d["columns"].values()} == {"string"}


@pytest.mark.parametrize("digits", [39, 41, 76])
def test_integers_wider_than_38_digits_are_float_as_in_the_baseline(kernel, tmp_path, digits):
    big = int("9" * digits)
    path = tmp_path / "t.csv"
    path.write_text(f"a\n{big}\n1\n")
    c = shape.profile(str(path)).to_dict()["columns"]["a"]
    assert c["dtype"] == "float"
    assert c["min_value"] == ["int", 1] and c["max_value"] == ["int", big]


def test_integers_wider_than_76_digits_are_refused_naming_the_column(tmp_path):
    path = tmp_path / "t.csv"
    path.write_text("wide\n" + "9" * 77 + "\n1\n")
    with pytest.raises(NotImplementedError, match="wide.*76 digits"):
        shape.profile(str(path))


# ---- #321: a folder of files with nothing in common is refused; the CLI compares sets ------


def test_a_folder_of_files_with_no_column_in_common_is_refused(tmp_path):
    (tmp_path / "x.csv").write_text("a,b\n1,2\n")
    (tmp_path / "y.csv").write_text("c,d\n3,4\n")
    with pytest.raises(ValueError, match=r"x\.csv.*y\.csv.*no column in common"):
        shape.profile(str(tmp_path))


def test_a_folder_of_files_with_the_same_columns_in_another_order_is_one_table(tmp_path, capsys):
    from shape.cli.main import main

    (tmp_path / "x.csv").write_text("a,b\n1,2\n")
    (tmp_path / "y.csv").write_text("b,a\n3,4\n")
    d = shape.profile(str(tmp_path)).to_dict()
    assert d["row_count"] == 2 and d["columns"]["a"]["null_count"] == 0
    assert main(["profile", str(tmp_path), "-o", str(tmp_path.parent / "o.shape")]) == 0


def test_a_folder_of_jsonl_files_with_other_keys_is_refused_by_the_cli(tmp_path, capsys):
    from shape.cli.main import main

    folder = tmp_path / "j"
    folder.mkdir()
    (folder / "x.jsonl").write_text('{"a": 1}\n')
    (folder / "y.jsonl").write_text('{"zz": "q"}\n')
    assert main(["profile", str(folder), "-o", str(tmp_path / "o.shape")]) == 2
    assert "do not share their columns" in capsys.readouterr().err
    with pytest.raises(ValueError, match="no column in common"):
        shape.profile(str(folder))


# ---- #322: the engine document is strict JSON when a float column overflows ---------------


@pytest.mark.parametrize("batch_size", [1 << 17, 7, 1])
def test_the_engine_document_is_strict_json_when_moments_overflow(kernel, batch_size):
    import json

    from shape.profile.engine import profile as engine_profile

    d = engine_profile(pa.table({"f": [1e308, -1e308, 0.0] * 100}), name="t", batch_size=batch_size)
    json.dumps(d, allow_nan=False)
    (col,) = d["tables"]["t"]["columns"]
    for key in ("mean", "m2", "variance_population", "variance_sample"):
        assert col[key] is None or math.isfinite(col[key])
    assert col["m2"] is None and col["variance_sample"] is None


# ---- #324: clear errors for unreadable inputs ----------------------------------------------


def test_a_csv_that_is_not_utf8_says_to_pass_its_encoding(tmp_path):
    path = tmp_path / "l.csv"
    path.write_bytes("name\ncafé\n".encode("latin-1"))
    with pytest.raises(ValueError, match=r"l\.csv.*not UTF-8.*encoding="):
        shape.profile(str(path))
    assert shape.profile(str(path), encoding="latin-1").to_dict()["row_count"] == 1


def test_an_empty_csv_file_is_named(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_bytes(b"")
    with pytest.raises(ValueError, match=r"empty\.csv.*empty"):
        shape.profile(str(path))


def test_an_unknown_time_zone_is_named_as_unknown(kernel):
    pytest.importorskip("zoneinfo").ZoneInfo("America/New_York")  # a database is present
    t = pa.table({"t": pa.array([0, 1], pa.timestamp("us", tz="Mars/Base"))})
    with pytest.raises(ValueError, match=r"unknown time zone 'Mars/Base'") as e:
        shape.profile(t)
    assert "tzdata" not in str(e.value)


@pytest.mark.parametrize("raw", ["abc", "1.5", "-1", "0x2"])
def test_profile_threads_must_be_a_positive_integer(monkeypatch, tmp_path, raw):
    path = tmp_path / "t.csv"
    path.write_text("a\n1\n")
    monkeypatch.setenv("PROFILE_THREADS", raw)
    with pytest.raises(
        ValueError, match=rf"PROFILE_THREADS must be a positive integer, got '{raw}'"
    ):
        shape.profile(str(path))


def test_profile_threads_one_does_not_change_the_process_thread_pools(monkeypatch, tmp_path):
    path = tmp_path / "t.csv"
    path.write_text("a\n1\n")
    before = (pa.cpu_count(), pa.io_thread_count())
    monkeypatch.setenv("PROFILE_THREADS", "1")
    shape.profile(str(path))
    assert (pa.cpu_count(), pa.io_thread_count()) == before


def test_engine_threads_must_not_be_negative():
    from shape.profile.engine import EngineOptions

    with pytest.raises(ValueError, match="threads must be a non-negative integer"):
        EngineOptions(threads=-1)


def test_file_urls_and_home_paths_are_read(tmp_path, monkeypatch):
    path = tmp_path / "x.csv"
    path.write_text("a\n1\n2\n")
    assert shape.profile(path.as_uri()).to_dict()["row_count"] == 2
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # what expanduser reads on Windows
    assert shape.profile("~/x.csv").to_dict()["row_count"] == 2


def test_an_empty_list_says_it_has_no_rows():
    with pytest.raises(ValueError, match="empty list"):
        shape.profile([])


def test_a_long_quotechar_is_a_value_error(tmp_path):
    path = tmp_path / "t.csv"
    path.write_text("a\n1\n")
    with pytest.raises(ValueError, match="quote character must be one character"):
        shape.profile(str(path), quotechar="ab")


def test_bytes_that_are_not_utf8_name_their_column(kernel):
    t = pa.table({"blob": pa.array([b"\xff\xfe", b"ok"], pa.binary())})
    with pytest.raises(ValueError, match="blob"):
        shape.profile(t)


# ---- #325: small correctness gaps ----------------------------------------------------------


def test_cramers_v_ignores_categories_whose_rows_were_dropped(kernel):
    from shape.profile.joint.measures import contingency, cramers_v

    a = ["x", "y", "z"] * 10 + [f"q{i}" for i in range(1, 6)] * 2
    b = ["p", "p", "r"] * 10 + [None] * 10
    cats = list(dict.fromkeys(a))  # x, y, z first: the 30 complete rows use codes 0 to 2
    ca = np.array([cats.index(v) for v in a])
    bcats = ["p", "r"]
    cb = np.array([-1 if v is None else bcats.index(v) for v in b])
    full = cramers_v(contingency(ca, cb, len(cats), 2))
    complete = cramers_v(contingency(ca[:30], cb[:30], 3, 2))
    assert full == pytest.approx(complete)


def test_a_dictionary_with_a_null_or_repeated_value_counts_right(kernel):
    nulls = _col(pa.DictionaryArray.from_arrays(pa.array([0, 1, 0]), pa.array(["x", None])))
    assert nulls["null_count"] == 1 and nulls["cardinality"] == 1
    assert nulls["value_counts_ext"] == {"x": 1.0}
    twice = _col(pa.DictionaryArray.from_arrays(pa.array([0, 1, 0, 1]), pa.array(["x", "x"])))
    assert twice["cardinality"] == 1 and twice["value_counts_ext"] == {"x": 1.0}


def test_a_long_duration_is_correlated(kernel):
    t = pa.table({"d": pa.array([10**10, 1, 2], pa.duration("s")), "x": [1.0, 2.0, 3.5]})
    m = shape.profile(t).to_dict()["correlation_matrix"]
    assert m["d"]["x"] == round(-0.802955, 4)  # the baseline's (pandas) value, rounded as stored


def test_profile_tables_are_copies(kernel):
    p = shape.profile(pa.table({"a": [1, 2, 3]}), name="t")
    p.tables[p.name]["row_count"] = 999
    assert p.to_dict()["row_count"] == 3
    ds = shape.profile({"t": pa.table({"a": [1, 2, 3]})})
    ds.tables["t"]["row_count"] = 999
    assert ds.to_dict()["tables"]["t"]["row_count"] == 3


def test_the_delimiter_warning_points_at_the_callers_line(tmp_path):
    path = tmp_path / "semi.csv"
    path.write_text("a;b\n1;2\n")
    with pytest.warns(UserWarning, match="delimiter") as rec:
        shape.profile(str(path), delimiter=",")
    assert rec[0].filename == __file__
    with pytest.warns(UserWarning, match="delimiter") as rec:
        shape.profile({"t": str(path)}, delimiter=",")
    assert rec[0].filename == __file__


def test_a_malformed_reference_pairs_spec_is_refused_before_profiling(monkeypatch):
    import importlib

    profile_mod = importlib.import_module("shape.profile.reference.profile")

    def boom(*a, **k):
        raise AssertionError("profiled before the spec was checked")

    monkeypatch.setattr(profile_mod, "_profile_cols_table", boom)
    t = pa.table({"a": [1, 2], "b": [3, 4]})
    for bad, msg in (
        ("zip", "list of"),
        ([{"columns": ["a"]}], "reference"),
        ([{"reference": "x"}], "columns"),
        ([{"columns": ["a", "nope"], "reference": "x"}], "nope"),
        ([{"columns": "a", "reference": "x"}], "list"),
    ):
        with pytest.raises(ValueError, match=msg):
            shape.profile(t, reference_pairs=bad)


# ---- #326: infer.py's date and datetime read the zone; the joint loop stops building views --


@pytest.mark.parametrize(
    ("hour_utc", "want"), [(5, "date"), (0, "datetime")]
)  # New York midnight is 05:00 UTC in January
def test_infer_reads_the_wall_clock_of_a_zoned_column(hour_utc, want):
    from shape.profile.infer import infer_column_type

    vals = [dt.datetime(2024, 1, d, hour_utc) for d in range(1, 6)]
    arr = pa.array(vals, pa.timestamp("us")).cast(pa.timestamp("us", tz="America/New_York"))
    assert infer_column_type(arr) == want
    assert _col(arr)["dtype"] == want


def test_the_joint_analysis_builds_no_view_for_a_column_it_cannot_use(monkeypatch):
    from shape.profile.joint import analyze

    built: list[str] = []
    real = analyze._build_view

    def counting(name, *a, **k):
        built.append(name)
        return real(name, *a, **k)

    monkeypatch.setattr(analyze, "_build_view", counting)
    cols = {f"t{i}": [f"v{(i + r) % 4}" for r in range(200)] for i in range(300)}
    shape.profile(pa.table(cols))
    assert len(built) == analyze.budget_for(200).max_columns
