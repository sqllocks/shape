"""#336: ``infer_column_type`` on text costs no more date parsing than profiling the column.

The profiler decides that a text column is not a date after one failed parse (it checks the
distinct values and stops at the first that does not parse); type inference used to parse every
distinct value with the per-value parser first. These tests count the parser calls rather than
time them, and check that the answers stay those of the profiler.
"""

from __future__ import annotations

import pyarrow as pa
import pytest

from shape.profile import infer
from shape.profile.reference import column, dtparse


@pytest.fixture
def parse_calls(monkeypatch):
    calls = {"n": 0}
    real = dtparse.parse_mixed

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(dtparse, "parse_mixed", counting)
    return calls


def _text(n: int) -> pa.Array:
    return pa.array([f"customer note {i} lorem" for i in range(n)])


@pytest.mark.parametrize("source", ["csv", "arrow"])
def test_ordinary_text_is_ruled_out_after_one_parse(parse_calls, source):
    assert infer.infer_column_type(_text(20_000), source) == "string"
    assert parse_calls["n"] <= 1


def test_text_with_nulls_is_ruled_out_after_one_parse(parse_calls):
    values = [None if i % 3 == 0 else f"word{i}" for i in range(9_000)]
    assert infer.infer_column_type(pa.array(values), "csv") == "string"
    assert parse_calls["n"] <= 1


def test_text_whose_first_value_is_a_date_is_ruled_out_at_the_first_other_value(parse_calls):
    values = ["01/05/2020"] + [f"name {i}" for i in range(5_000)]
    assert infer.infer_column_type(pa.array(values), "csv") == "string"
    assert parse_calls["n"] <= 2


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["2020-01-05", "2021-12-31", None], "datetime"),  # ISO dates (bulk path)
        (["2020-01-05 10:00:00", "2021-12-31 23:59:59"], "datetime"),
        (["2020-01-05T10:00:00.123", "2021-12-31T23:59:59.5"], "datetime"),
        (["01/05/2020", "12/31/2021", None], "datetime"),  # non-ISO: per-value parser
        (["2020/1/5", "2021/12/31"], "datetime"),
        (["Jan 5 2020", "Dec 31 2021"], "datetime"),
        (["2020-01-05", "not a date"], "string"),  # ISO first value, a later value fails
        (["01/05/2020", "not a date"], "string"),
        (["hello", "2020-01-05"], "string"),
        (["", "x"], "string"),
        ([None, None], "string"),
        (["yes", "no", "YES"], "boolean"),
        (["1", "2", "3"], "integer"),
        (["1.5", "2"], "float"),
    ],
)
def test_the_type_is_the_profilers(values, expected):
    arr = pa.array(values, pa.string())
    assert infer.infer_column_type(arr, "csv") == expected
    assert infer.infer_column_type(arr, "arrow") == expected


def test_a_non_iso_date_column_is_still_a_date():
    values = [f"{m}/{d}/2020" for m in range(1, 13) for d in range(1, 29)]
    assert infer.infer_column_type(pa.array(values), "csv") == "datetime"
    assert infer.infer_column_type(pa.array([*values, "13/45/2020"]), "csv") == "string"


def test_inference_uses_the_profilers_datetime_check(monkeypatch):
    """No full per-value coercion of a non-ISO text column before the cheap check."""
    seen = []
    real = column._coerce_datetime_strings

    def spy(arr, keep_nulls=False):
        seen.append(len(arr))
        return real(arr, keep_nulls)

    monkeypatch.setattr(infer, "_coerce_datetime_strings", spy, raising=False)
    assert infer.infer_column_type(_text(1_000), "csv") == "string"
    assert seen == []
