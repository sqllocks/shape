"""P1-18: a column is an enum only if its values repeat (distinct <= 0.5 x non-null values) and
it is not unique, on top of the size limits. The same decision in both kernels (Python twin and
Rust), the object-dtype path, bounded mode, and everything that reads ``is_enum``."""

from __future__ import annotations

import json

import numpy as np
import pyarrow as pa
import pytest

import shape
from shape.kernel import dispatch, reference
from shape.profile.engine import profile as engine_profile


@pytest.fixture(params=["python", "rust"])
def kernel(request, monkeypatch):
    if request.param == "rust":
        pytest.importorskip("shape._kernel")
    monkeypatch.setenv("SHAPE_KERNEL", request.param)
    dispatch.reset()
    yield request.param
    dispatch.reset()


def _col(values, kind=None):
    """The profile of one column of ``values`` (a pyarrow array or list)."""
    arr = values if isinstance(values, pa.Array) else pa.array(values, type=kind)
    return shape.profile(pa.table({"c": arr})).to_dict()["columns"]["c"]


def test_unique_column_is_never_an_enum(kernel):
    for values in (list(range(150)), [f"u{i}" for i in range(150)], [float(i) for i in range(30)]):
        col = _col(values)
        assert col["is_unique"] is True
        assert col["is_enum"] is False and col["enum_values"] is None
        assert col["value_counts_ext"]  # the top values are still reported


def test_tiny_table_of_distinct_text_is_not_an_enum(kernel):
    col = _col([f"name {c}" for c in "abcdefghij"])
    assert col["cardinality"] == 10
    assert col["is_enum"] is False and col["enum_values"] is None


def test_free_text_is_not_an_enum(kernel):
    rng = np.random.default_rng(1)
    words = ["alpha", "beta", "gamma", "delta", "eps", "zeta", "eta", "theta", "iota", "kappa"]
    text = [" ".join(rng.choice(words, 5)) + f" {i}" for i in range(180)]
    col = _col(text)
    assert col["is_enum"] is False and col["enum_values"] is None


def test_low_cardinality_category_stays_an_enum(kernel):
    values = ["red", "green", "blue", "red", "green", "red"] * 40
    col = _col(values)
    assert col["is_enum"] is True
    assert col["enum_values"] == {"red": 0.5, "green": 0.333333, "blue": 0.166667}
    ints = _col([1, 2, 3, 1, 2, 1] * 40)
    assert ints["is_enum"] is True and set(ints["enum_values"]) == {"1", "2", "3"}


@pytest.mark.parametrize("distinct,expected", [(4, True), (5, True), (6, False), (7, False)])
def test_half_boundary(kernel, distinct, expected):
    """10 non-null values: at most 5 distinct is an enum, 6 is not."""
    values = [i % distinct for i in range(distinct)] + [0] * (10 - distinct)
    assert len(set(values)) == distinct
    for arr in (pa.array(values), pa.array([f"v{v}" for v in values])):
        col = _col(arr)
        assert col["is_enum"] is expected
        assert (col["enum_values"] is not None) is expected


def test_boundary_counts_non_null_values_only(kernel):
    # 5 distinct among 8 non-null values (2 nulls): 5 > 4, not an enum
    col = _col(["a", "b", "c", "d", "e", "a", "b", "c", None, None])
    assert col["is_enum"] is False
    # 4 distinct among 8 non-null values: an enum
    col = _col(["a", "b", "c", "d", "a", "b", "c", "d", None, None])
    assert col["is_enum"] is True


def test_all_null_and_one_value_columns(kernel):
    nulls = _col(pa.nulls(20, pa.string()))
    assert nulls["is_enum"] is False and nulls["enum_values"] is None
    floats = _col(pa.nulls(20, pa.float64()))
    assert floats["is_enum"] is False and floats["enum_values"] is None
    same = _col(["x"] * 20)
    assert same["is_enum"] is True and same["enum_values"] == {"x": 1.0}
    same_num = _col([7] * 20)
    assert same_num["is_enum"] is True and same_num["enum_values"] == {"7": 1.0}
    single = _col(["x"])  # one row: one distinct value, unique
    assert single["is_unique"] is True and single["is_enum"] is False


def test_object_dtype_path(kernel):
    import datetime as dt
    from decimal import Decimal

    cases = {
        "decimal_dup": ([Decimal("1.5"), Decimal("2.5")] * 5, True),
        "decimal_unique": ([Decimal(i) for i in range(10)], False),
        "time_dup": ([dt.time(1, 0), dt.time(2, 0)] * 5, True),
        "time_unique": ([dt.time(i, 0) for i in range(10)], False),
    }
    for name, (values, expected) in cases.items():
        col = _col(pa.array(values))
        assert col["is_enum"] is expected, name
        assert (col["enum_values"] is not None) is expected, name


def test_top_values_are_kept_for_every_column(kernel):
    """``value_counts_ext`` (first 500) does not depend on the enum decision."""
    unique = _col([f"k{i}" for i in range(700)])
    # (ISS-profile #37: the top 500 of a text column whose values are all different would be an
    # arbitrary few, so a near-unique text column lists none; a numeric one still does)
    assert unique["value_counts_ext"] is None and unique["enum_values"] is None
    unique_num = _col(list(range(700)))
    assert len(unique_num["value_counts_ext"]) == 500 and unique_num["enum_values"] is None
    cat = _col([f"k{i % 120}" for i in range(2000)])
    assert cat["is_enum"] is True
    assert len(cat["enum_values"]) == 120 and set(cat["value_counts_ext"]) == set(
        cat["enum_values"]
    )
    mid = _col([f"k{i // 2}" for i in range(2000)])  # 1000 distinct, exactly half
    assert mid["is_enum"] is False  # card >= 200 and ratio 0.5 >= 0.30
    assert len(mid["value_counts_ext"]) == 500


# ---------------------------------------------------------------- the two kernels agree
def _count(kern, values, top_n, row_count):
    out = kern.count_numeric(pa.array(values), top_n, row_count, False, False)
    return out["cardinality"], out["keys"].to_pylist(), out["counts"].to_pylist()


@pytest.mark.parametrize("seed", range(40))
def test_rust_and_python_twins_are_identical(seed):
    native = pytest.importorskip("shape._kernel")
    rng = np.random.default_rng(seed)
    n = int(rng.choice([1, 2, 3, 9, 10, 11, 60, 199, 200, 201, 500, 1000]))
    card = max(1, int(n * rng.choice([0.01, 0.2, 0.3, 0.49, 0.5, 0.51, 0.99, 1.0])))
    values = rng.integers(0, card, n).astype(np.int64)
    for v in (values, values / 4.0):
        for top_n in (5, 500):
            for row_count in (n, n + int(rng.integers(0, 4 * n + 1))):
                assert _count(native, v, top_n, row_count) == _count(reference, v, top_n, row_count)


@pytest.mark.parametrize(
    "n,card,keeps_all",
    [
        (10, 5, True),
        (10, 6, False),
        (4, 2, True),
        (3, 3, False),
        (400, 150, True),
        (400, 250, False),
    ],
)
def test_numeric_kernels_keep_every_key_only_for_an_enum(n, card, keeps_all):
    values = np.arange(n) % card
    for kern in (reference, pytest.importorskip("shape._kernel")):
        got_card, keys, _ = _count(kern, values, 1, n)  # top_n 1: only an enum keeps every key
        assert got_card == card
        assert (len(keys) == card) is keeps_all


# ---------------------------------------------------------------- bounded mode
def test_bounded_mode_has_no_enum_flag_and_exact_mode_agrees(kernel):
    """The fused engine (exact and bounded) reports ``top`` lists and never an ``is_enum`` /
    ``enum_values`` decision, so the rule has nothing to apply there; the two modes still share
    one layout."""
    table = pa.table({"uniq": [f"u{i}" for i in range(50)], "cat": ["a", "b"] * 25})
    for mode in ("exact", "bounded"):
        doc = engine_profile(table, name="t", mode=mode)["tables"]["t"]
        for col in doc["columns"]:
            assert "is_enum" not in col and "enum_values" not in col


# ---------------------------------------------------------------- downstream consumers
def _tiny_profile():
    table = pa.table(
        {
            "name": [f"person {i}" for i in range(12)],
            "email": [f"p{i}@example.com" for i in range(12)],
            "tier": ["gold", "silver", "gold", "bronze"] * 3,
        }
    )
    return shape.profile(table, name="people")


def test_summary_html_and_json_roundtrip(kernel):
    prof = _tiny_profile()
    cols = prof.to_dict()["columns"]
    assert [cols[c]["is_enum"] for c in ("name", "email", "tier")] == [False, False, True]
    assert json.loads(json.dumps(prof.to_dict()))["columns"]["tier"]["enum_values"] == {
        "gold": 0.5,
        "silver": 0.25,
        "bronze": 0.25,
    }
    assert set(prof.summary()["columns"]) == {"name", "email", "tier"}
    html = prof.to_html()  # a non-enum column falls back to its top values for the bars
    assert "person 0" in html and "gold" in html


def test_diff_and_check_still_work(kernel):
    from shape.contracts import v1

    base = _tiny_profile()
    assert v1.diff(base, _tiny_profile()).changes == []
    cur = shape.profile(
        pa.table(
            {
                "name": [f"person {i}" for i in range(12)],
                "email": [f"p{i}@example.com" for i in range(12)],
                "tier": ["gold", "silver", "gold", "platinum"] * 3,
            }
        ),
        name="people",
    )
    assert [c["kind"] for c in v1.diff(base, cur).changes] == ["new_categorical_values"]
    contract = {"columns": {"name": {"allowed_values": ["person 0"]}}}
    assert v1.check(base, contract).violations  # driven by the top values, not by is_enum
