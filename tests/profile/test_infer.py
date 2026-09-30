"""P1-05: type inference (Spindle's dtype rules) and the capture-path fixes P2, P3, P4."""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest

import shape
from shape.capture import capture_rows
from shape.io import PANDAS_CSV, read_table
from shape.profile.infer import TypeTracker, as_number, infer_column_type, is_number

ROOT = Path(__file__).resolve().parents[2]


# ------------------------------------------------------------------ dtype rules


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (pa.array([True, False, True]), "boolean"),
        (pa.array([True, None]), "boolean"),
        (pa.array([1, 2, 3]), "integer"),
        (pa.array([1, None, 3]), "integer"),  # int with nulls: float64 in pandas, whole numbers
        (pa.array([1.0, 2.0, None]), "integer"),  # a float column of whole numbers
        (pa.array([1.5, 2.0]), "float"),
        (pa.array([float("nan"), 1.0]), "integer"),
        (pa.array([float("nan"), 1.5]), "float"),
        (pa.array([None, None], type=pa.float64()), "float"),
        (pa.array([dt.datetime(2020, 1, 1), dt.datetime(2020, 1, 2)]), "date"),
        (pa.array([dt.datetime(2020, 1, 1, 5)]), "datetime"),
        (pa.array([dt.date(2020, 1, 1)]), "datetime"),  # object dtype of datetime.date
        (pa.array([None, None], type=pa.null()), "string"),
        (pa.array(["a", "b"]), "string"),
        (pa.array(["Yes", "no", "YES"]), "boolean"),
        (pa.array(["1", "0", "1"]), "boolean"),
        (pa.array(["10", "20", "30"]), "integer"),
        (pa.array(["1.5", "2"]), "float"),
        (pa.array(["2020-01-05", "2020-02-06"]), "datetime"),
        (pa.array(["2020-01-05", "nope"]), "string"),
        (pa.array(["a", None], type=pa.large_string()), "string"),
        (pa.array([Decimal("1.5"), Decimal("2")]), "float"),
    ],
)
def test_arrow_type_rules(values, expected):
    assert infer_column_type(values, source="arrow") == expected


def test_csv_source_keeps_dates_as_text_like_read_csv():
    text = pa.array(["2020-01-05", "2020-02-06"])
    assert infer_column_type(text, source="csv") == "datetime"  # via string parsing
    assert infer_column_type(pa.array([1, 2, None]), source="csv") == "integer"
    assert infer_column_type(pa.array([True, None]), source="csv") == "boolean"
    with pytest.raises(ValueError, match="source"):
        infer_column_type(pa.array([1]), source="xml")


def test_chunked_arrays_and_python_lists_are_accepted():
    assert infer_column_type(pa.chunked_array([pa.array([1]), pa.array([2])])) == "integer"
    assert infer_column_type([1.5, 2.5]) == "float"


# ------------------------------------------------ dtype parity on the T-22 datasets


def _datasets_module():
    path = ROOT / "benchmarks" / "vs_spindle" / "profile_1to1" / "datasets.py"
    spec = importlib.util.spec_from_file_location("vs_spindle_datasets", path)
    assert spec and spec.loader
    sys.path.insert(0, str(path.parents[1]))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["vs_spindle_datasets"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def t22_data(tmp_path_factory):
    """D1, D2 (50k rows), D4, MT and every EDGE file, written by the benchmark generator."""
    out = tmp_path_factory.mktemp("t22")
    ds = _datasets_module()
    ds.OUT = out
    ds.d1()
    ds.write(ds._d2_table(50_000), "d2")
    ds.d4()
    ds.mt()
    ds.edge()
    return out


def _files(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*") if p.suffix in (".csv", ".parquet") and p.parent.name != "mt"
    )


def test_dtype_of_every_column_matches_the_spindle_profiler(t22_data):
    """The reference profiler equals Spindle's DataProfiler field by field (verify.py, 30/30),
    so its dtype is Spindle's dtype; infer_column_type must give the same for each column."""
    files = _files(t22_data)
    assert len(files) >= 22
    checked = 0
    for path in files:
        csv = path.suffix == ".csv"
        table = read_table(path, csv=PANDAS_CSV) if csv else read_table(path)
        want = shape.profile(str(path)).to_dict()
        # single-table profile dicts are {"tables": {name: {...}}} for the multi-table form
        cols = (
            next(iter(want["tables"].values()))["columns"] if "tables" in want else want["columns"]
        )
        for name in table.column_names:
            got = infer_column_type(table[name], source="csv" if csv else "arrow")
            assert got == cols[name]["dtype"], (path.name, name, got, cols[name]["dtype"])
            checked += 1
    assert checked > 300


def test_dtype_of_multi_table_dataset(t22_data):
    for name in ("customer", "product", "orders"):
        path = t22_data / "mt" / f"{name}.csv"
        table = read_table(path, csv=PANDAS_CSV)
        want = shape.profile({name: str(path)}).to_dict()["tables"][name]["columns"]
        for col in table.column_names:
            assert infer_column_type(table[col], source="csv") == want[col]["dtype"], (name, col)


# ------------------------------------------- capture path: P2, P3, P4 (evidence is kept)


def test_p2_demotion_to_text_keeps_every_value():
    """[1, 2, "x", 3] used to give count 3: the values before the text value were dropped."""
    cap = capture_rows({"v": v} for v in [1, 2, "x", 3]).to_dict()
    col = cap["columns"]["v"]
    assert col["kind"] == "text" and col["count"] == 4 and col["null_count"] == 0
    assert {value for value, _, _ in col["topk"]} == {"1", "2", "x", "3"}
    assert col["distinct_estimate"] == pytest.approx(4, abs=0.5)
    assert col["length"]["count"] == 4


def test_p3_a_leading_none_does_not_lock_the_column_as_text():
    col = capture_rows({"v": v} for v in [None, 1, 2]).to_dict()["columns"]["v"]
    assert col["kind"] == "numeric" and col["count"] == 3 and col["null_count"] == 1
    assert col["finite_count"] == 2 and col["mean"] == 1.5
    col = capture_rows([{"v": None}, {"v": None}]).to_dict()["columns"]["v"]
    assert col["kind"] == "text" and col["null_count"] == 2  # nothing else to go on


def test_p4_numpy_numbers_and_decimal_are_numeric():
    rows = [
        {"a": np.int64(3), "b": Decimal("1.5"), "c": np.float32(2.5)},
        {"a": np.int64(5), "b": Decimal("2.5"), "c": np.float32(3.5)},
    ]
    cols = capture_rows(rows).to_dict()["columns"]
    assert {c["kind"] for c in cols.values()} == {"numeric"}
    assert cols["a"]["mean"] == 4 and cols["b"]["mean"] == 2.0 and cols["c"]["mean"] == 3.0
    assert not is_number(True) and not is_number(np.bool_(True)) and not is_number("1")
    assert as_number(np.int64(7)) == 7 and isinstance(as_number(np.int64(7)), int)
    assert as_number(Decimal("0.25")) == 0.25


def test_bool_columns_are_text_and_numeric_columns_still_work():
    cols = capture_rows(
        [{"flag": True, "n": 1, "x": 0.5}, {"flag": False, "n": 2, "x": None}]
    ).to_dict()["columns"]
    assert cols["flag"]["kind"] == "text" and cols["n"]["kind"] == "numeric"
    assert cols["x"]["null_count"] == 1 and cols["x"]["finite_count"] == 1


def test_missing_keys_and_late_columns_count_as_nulls():
    cap = capture_rows([{"a": 1}, {"a": 2, "b": "x"}, {"b": "y"}]).to_dict()
    assert cap["rows"] == 3
    assert cap["columns"]["a"]["null_count"] == 1 and cap["columns"]["a"]["count"] == 3
    assert cap["columns"]["b"]["null_count"] == 1 and cap["columns"]["b"]["count"] == 3


def test_type_tracker():
    t = TypeTracker()
    for v in [None, 1, 2.5]:
        t.observe(v)
    assert t.kind == "numeric" and (t.count, t.null_count, t.numeric_count) == (3, 1, 2)
    t.observe("x")
    assert t.kind == "text" and t.other_count == 1
    assert TypeTracker().kind == "text"
