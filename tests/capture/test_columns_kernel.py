"""P1-06: kernel-backed capture_columns; regressions P10, P11, P12, P13."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pyarrow as pa
import pytest

import shape.capture as capture_pkg
from shape.capture import capture_columns, capture_rows
from shape.profile.text_vectorized import profile_text_semantic


def _shape(d):
    """The nested key structure of a captured column. List contents are not compared, and
    an error model's ``parameters`` are free-form per algorithm."""
    if isinstance(d, dict):
        return {k: (None if k == "parameters" else _shape(v)) for k, v in d.items()}
    return None


def test_p10_arrow_nulls_are_counted_not_turned_into_nan():
    cols = capture_columns(
        {
            "i": pa.array([1, None, 3, None]),
            "f": pa.array([1.5, None, float("nan"), 2.5]),
            "n": np.array([1.0, np.nan, 3.0, np.nan]),
        }
    )["columns"]
    assert cols["i"]["null_count"] == 2 and cols["i"]["finite_count"] == 2
    assert cols["f"]["null_count"] == 1 and cols["f"]["nan_count"] == 1
    assert cols["n"]["null_count"] == 0 and cols["n"]["nan_count"] == 2  # NumPy NaN is NaN
    assert cols["i"]["mean"] == 2.0


def test_p11_none_is_not_the_text_none():
    prof, _ = profile_text_semantic(["a", None, "b", None, "a"])
    assert prof["count"] == 5 and prof["null_count"] == 2
    assert all(v != "None" for v, _ in prof["topk"]) and prof["distinct_estimate"] == 2
    prof, _ = profile_text_semantic(np.array(["x", None, "y"], dtype=object))
    assert prof["null_count"] == 1 and prof["min_length"] == 1
    assert profile_text_semantic([None, None])[0]["null_count"] == 2
    assert profile_text_semantic([])[0]["count"] == 0
    _, sem = profile_text_semantic(["a@b.co", "c@d.org", None])
    assert sem and sem[0][0] == "email" and sem[0][1] == 1.0  # over the non-null values


DATA = {
    "n": np.arange(1000, dtype=np.int64),
    "x": np.linspace(0, 1, 1000),
    "s": [f"v{i % 17}" for i in range(1000)],
    "t": pa.array(["a", None, "bb"] * 333 + ["c"]),
}


def _rows():
    n = len(DATA["n"])
    return [
        {
            "n": int(DATA["n"][i]),
            "x": float(DATA["x"][i]),
            "s": DATA["s"][i],
            "t": DATA["t"][i].as_py(),
        }
        for i in range(n)
    ]


def test_p12_column_and_row_paths_emit_the_same_schema():
    fast = capture_columns(DATA)["columns"]
    slow = capture_rows(_rows()).to_dict()["columns"]
    assert set(fast) == set(slow)
    for name in slow:
        assert _shape(fast[name]) == _shape(slow[name]), name
    # ... and agree on the numbers the two paths both compute exactly
    for name in ("n", "x"):
        for key in ("count", "null_count", "finite_count", "min", "max"):
            assert fast[name][key] == slow[name][key], (name, key)
        assert fast[name]["mean"] == pytest.approx(slow[name]["mean"])
        assert fast[name]["variance_population"] == pytest.approx(slow[name]["variance_population"])
    for name in ("s", "t"):
        assert fast[name]["count"] == slow[name]["count"]
        assert fast[name]["null_count"] == slow[name]["null_count"]
        assert fast[name]["length"]["max"] == slow[name]["length"]["max"]
        assert fast[name]["length"]["mean"] == pytest.approx(slow[name]["length"]["mean"])
        assert fast[name]["distinct_estimate"] == pytest.approx(
            slow[name]["distinct_estimate"], abs=0.5
        )


def test_p12_fallback_kinds_have_the_row_path_schema():
    cols = {
        "flag": [True, False, True],
        "when": [dt.datetime(2020, 1, 1), dt.datetime(2020, 1, 2), None],
        "mixed": [1, "x", 3],
    }
    fast = capture_columns(cols)["columns"]
    slow = capture_rows([{k: v[i] for k, v in cols.items()} for i in range(3)]).to_dict()["columns"]
    for name in cols:
        assert _shape(fast[name]) == _shape(slow[name]), name
    assert fast["mixed"]["kind"] == "text" and fast["mixed"]["count"] == 3


def test_p13_text_columns_do_not_fall_back_to_per_cell_python(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("row path used for a text column")

    monkeypatch.setattr(capture_pkg.core, "capture_rows", boom)
    monkeypatch.setattr("shape.capture.capture_rows", boom)
    out = capture_columns({"s": [f"k{i % 5}" for i in range(500)], "n": np.arange(500)})["columns"]
    assert out["s"]["kind"] == "text" and out["s"]["distinct_estimate"] == 5
    assert out["s"]["topk"][0][1] == 100


def test_bounded_mode_and_empty_input():
    out = capture_columns(
        {"k": np.arange(20000), "s": [f"v{i}" for i in range(20000)]}, mode="bounded"
    )
    k = out["columns"]["k"]
    assert k["error_models"]["cardinality"]["algorithm"] == "hyperloglog"
    assert abs(k["distinct_estimate"] - 20000) / 20000 < 0.05
    assert len(out["columns"]["s"]["topk"]) <= 10
    assert capture_columns({}) == {"rows": 0, "columns": {}}
    with pytest.raises(ValueError, match="equal length"):
        capture_columns({"a": [1, 2], "b": [1]})
