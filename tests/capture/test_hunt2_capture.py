"""HUNT2-profile: regression tests for capture (#685, #687, #688), on both kernels."""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np
import pyarrow as pa
import pytest

from shape.capture.core import capture_arrow, capture_columns, capture_rows
from shape.kernel import dispatch


@pytest.fixture(params=["rust", "python"], autouse=True)
def kernel(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("SHAPE_KERNEL", request.param)
    dispatch.reset()
    yield request.param
    dispatch.reset()


# ------------------------------------------------------------------- #685 exact integers

_BIG = [9007199254740993, 9007199254740992, 9007199254740995, 7]


def test_685_rows_keep_integers_exact_like_columns() -> None:
    cols = capture_columns({"id": _BIG})["columns"]["id"]
    rows = capture_rows([{"id": v} for v in _BIG]).to_dict()["columns"]["id"]
    for key in ("kind", "min", "max", "distinct_estimate", "topk", "count", "null_count"):
        assert rows[key] == cols[key], key
    assert rows["max"] == 9007199254740995 and type(rows["max"]) is int
    assert rows["distinct_estimate"] == 4


def test_685_arrow_keeps_integers_exact() -> None:
    doc = capture_arrow(pa.table({"id": pa.array(_BIG, pa.int64())})).to_dict()["columns"]["id"]
    assert doc["max"] == 9007199254740995 and doc["distinct_estimate"] == 4


def test_685_numpy_integers_with_nulls_stay_integers() -> None:
    rows = [{"x": np.int64(3)}, {"x": None}, {"x": 5}, {"x": np.int32(-2)}]
    doc = capture_rows(rows).to_dict()["columns"]["x"]
    assert (doc["min"], doc["max"], doc["null_count"]) == (-2, 5, 1)
    assert type(doc["min"]) is int


@pytest.mark.parametrize(
    ("values", "expected_max"),
    [
        ([1, 2, 2.5], 2.5),  # a float makes the column float
        ([1, 2**63], float(2**63)),  # past int64: float, as before
        ([1, -(2**63) - 1], 1.0),
    ],
)
def test_685_mixed_or_wide_numbers_are_floats(values: list, expected_max: float) -> None:
    doc = capture_rows([{"x": v} for v in values]).to_dict()["columns"]["x"]
    assert doc["kind"] == "numeric"
    assert doc["max"] == expected_max and type(doc["max"]) is float


def test_685_late_float_after_many_ints(kernel: str) -> None:
    rows = [{"x": i} for i in range(25)] + [{"x": 0.5}]
    doc = capture_rows(rows, batch_size=10).to_dict()["columns"]["x"]
    assert doc["min"] == 0.0 and type(doc["min"]) is float and doc["max"] == 24.0


# ---------------------------------------------------------------- #687 empty other columns


@pytest.mark.parametrize(
    "columns",
    [
        {"a": []},
        {"flag": pa.array([], pa.bool_())},
        {"n": pa.array([], pa.null())},
        {"x": [], "s": pa.array([], pa.string())},
        {"d": pa.array([], pa.date32())},
    ],
)
def test_687_empty_columns_of_any_type_capture(columns: dict) -> None:
    doc = capture_columns(columns)
    assert doc["rows"] == 0
    assert list(doc["columns"]) == list(columns)
    for col in doc["columns"].values():
        assert col["count"] == 0 and col["null_count"] == 0


def test_687_a_non_empty_bool_column_still_captures() -> None:
    doc = capture_columns({"flag": pa.array([True, None, False])})
    assert doc["rows"] == 3 and doc["columns"]["flag"]["count"] == 3


# ------------------------------------------------------------------- #688 non-text bytes


def test_688_binary_that_is_utf8_is_captured_as_its_text() -> None:
    table = pa.table({"name": pa.array(["café".encode(), b"tea", None], pa.binary())})
    doc = capture_arrow(table).to_dict()["columns"]["name"]
    assert {v for v, _c, _e in doc["topk"]} == {"café", "tea"}
    assert doc["length"]["max"] == 4


def test_688_binary_that_is_not_utf8_is_refused_by_name() -> None:
    table = pa.table({"name": pa.array([b"caf\xe9"], pa.binary())})
    with pytest.raises(ValueError, match="'name'.*UTF-8"):
        capture_arrow(table)


def test_688_rows_with_bytes_are_refused_or_decoded() -> None:
    doc = capture_rows([{"b": b"abc"}]).to_dict()["columns"]["b"]
    assert doc["topk"][0][0] == "abc"
    with pytest.raises(ValueError, match="'b'.*UTF-8"):
        capture_rows([{"b": b"\xff\x00"}])
