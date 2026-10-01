"""P7-02: no released cell below the minimum cohort (property tests) and the release surfaces."""

from __future__ import annotations

import copy
import hashlib
import math
from datetime import datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import shape
from shape.privacy import release_for, suppress_shape
from shape.privacy.cells import (
    OTHER_BUCKET,
    count_lower_bound,
    suppress_bins,
    suppress_column_cells,
    suppress_counts,
    suppress_weights,
)
from shape.privacy.release import SuppressionPolicy
from shape.privacy.safe_profile import ColumnConfig, SafeConfig, to_safe_profile

# Counts up to 2.5M cross the point (n = 1M) where six-place proportions stop being exact.
counts_st = st.lists(st.integers(1, 40), min_size=1, max_size=14)
big_counts_st = st.lists(st.integers(1, 2_500_000), min_size=1, max_size=6)
k_st = st.integers(2, 15)


def weights_of(counts: list[int]) -> dict[str, float]:
    n = sum(counts)
    return {f"c{i}": round(c / n, 6) for i, c in enumerate(counts)}


@given(st.one_of(counts_st, big_counts_st), k_st)
@settings(max_examples=300, deadline=None)
def test_released_weights_never_stand_for_fewer_than_k_rows(counts, k):
    n = sum(counts)
    weights = weights_of(counts)
    before = copy.deepcopy(weights)
    released, folded = suppress_weights(weights, k, n)
    assert weights == before  # the input is untouched
    true = {f"c{i}": c for i, c in enumerate(counts)}
    for key, w in released.items():
        if key != OTHER_BUCKET:
            assert true[key] >= k and w == before[key]
    hidden = sum(c for key, c in true.items() if key not in released)
    if n < k:
        assert released == {}  # the whole column is below k rows: nothing is released
    else:
        if released.get(OTHER_BUCKET, 0) > 0:
            assert hidden >= k  # the residual bucket is itself a cell
        elif OTHER_BUCKET not in released:
            assert hidden == 0
        # A zero-weight bucket releases no mass: rows below the six-place rounding step.
        assert math.isclose(sum(released.values()), sum(before.values()), abs_tol=1e-5)
    assert folded == len(true) - sum(1 for key in released if key != OTHER_BUCKET)


@given(counts_st, k_st)
@settings(max_examples=200, deadline=None)
def test_released_counts_never_below_k(counts, k):
    mapping = {f"c{i}": c for i, c in enumerate(counts)}
    released, _ = suppress_counts(mapping, k)
    assert all(v == 0 or v >= k for v in released.values())
    assert sum(released.values()) == (sum(counts) if sum(counts) >= k else 0)
    assert all(mapping[key] == v for key, v in released.items() if key != OTHER_BUCKET)


@given(st.lists(st.integers(0, 60), min_size=1, max_size=24), k_st, st.booleans())
@settings(max_examples=300, deadline=None)
def test_released_histogram_bins_are_zero_or_at_least_k(counts, k, as_proportions):
    n = sum(counts)
    if n == 0:
        return
    if as_proportions:
        bins, dropped = suppress_bins([round(c / n, 6) for c in counts], k, n)
    else:
        bins, dropped = suppress_bins(counts, k, None, proportions=False)
    if bins is None:
        assert all(c < k for c in counts if c)  # nothing releasable
        return
    for c, b in zip(counts, bins, strict=True):
        if b:  # a released bin
            assert c >= k
    assert dropped == sum(1 for c, b in zip(counts, bins, strict=True) if c and not b)
    if as_proportions:
        assert math.isclose(sum(bins), 1.0, abs_tol=1e-5)


def test_k_of_one_or_no_base_passes_through():
    w = {"a": 0.9, "b": 0.1}
    assert suppress_weights(w, 1, 10) == (w, 0)
    assert suppress_weights(w, 5, None) == (w, 0)
    assert suppress_weights(w, 5, 0) == (w, 0)
    assert suppress_counts({"a": 1}, 1) == ({"a": 1}, 0)
    assert suppress_bins([0.5, 0.5], 5, None) == ([0.5, 0.5], 0)


def test_lower_bound_is_exact_up_to_a_million_rows_and_conservative_above():
    for n in (10, 999, 1_000_000):
        for c in (1, 5, n // 3):
            assert count_lower_bound(round(c / n, 6), n) == c
    n = 3_000_000
    assert count_lower_bound(round(7 / n, 6), n) <= 7


def test_other_bucket_absorbs_the_smallest_survivor_and_the_empty_column_is_withheld():
    # n=100: c=3 is folded, so __OTHER__ would hold 3 < k=5: the smallest survivor (c=17) joins.
    released, folded = suppress_weights({"a": 0.60, "b": 0.20, "c": 0.17, "d": 0.03}, 5, 100)
    assert set(released) == {"a", "b", OTHER_BUCKET} and folded == 2
    assert released[OTHER_BUCKET] == pytest.approx(0.20)
    assert suppress_weights({"a": 0.5, "b": 0.5}, 5, 4) == ({}, 2)  # 4 rows in the column


def test_proportion_histogram_zeroes_small_bins_and_renormalizes():
    bins, dropped = suppress_bins([0.5, 0.48, 0.02], 5, 100)
    assert dropped == 1 and bins == [0.510204, 0.489796, 0]
    assert suppress_bins([0.02, 0.03], 5, 100) == (None, 2)


# ---- end to end: build a profile from real rows, then audit every released cell -------------

row_st = st.tuples(
    st.sampled_from(["red", "blue", "green", "teal", "rose", "gray", "gold"]),
    st.integers(100, 105),
    st.integers(0, 3),  # day offset
    st.integers(0, 23),  # hour
)


def _write(path, rows):
    lines = ["label,store,stamp"]
    for label, store, day, hour in rows:
        lines.append(f"{label},{store},2021-0{1 + day}-1{day + 1} {hour:02d}:30:00")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@given(
    st.lists(row_st, min_size=8, max_size=160),
    st.lists(st.integers(0, 6), min_size=0, max_size=40),
    st.integers(2, 12),
)
@settings(max_examples=60, deadline=None)
def test_no_released_cell_in_a_safe_profile_is_below_the_minimum(tmp_path_factory, rows, skew, k):
    # skew piles extra rows on the first label so frequencies are uneven.
    rows = rows + [("red", 100, 0, 0)] * len(skew)
    path = tmp_path_factory.mktemp("prop") / "t.csv"
    _write(path, rows)
    safe = to_safe_profile(shape.profile(str(path)), SafeConfig(k=k))
    (table,) = safe.tables.values()
    n = table.row_count
    assert n == len(rows)

    label = table.columns["label"]
    truth = {v: sum(1 for r in rows if r[0] == v) for v in {r[0] for r in rows}}
    cells = label.categorical_weights or {}
    seen = {key for key in cells if key != OTHER_BUCKET}
    for key in seen:
        assert truth[key] >= k, (key, truth, k)
    hidden = sum(c for v, c in truth.items() if v not in seen)
    if OTHER_BUCKET in cells:
        assert hidden >= k
    else:
        assert hidden == 0 or not cells  # nothing hidden unless the whole column was withheld

    store = table.columns["store"]
    tstore = {s: sum(1 for r in rows if r[1] == s) for s in {r[1] for r in rows}}
    if store.categorical_weights:  # label route is closed for numbers: hashed keys only
        hashed = {hashlib.sha256(str(s).encode()).hexdigest()[:12]: c for s, c in tstore.items()}
        for key in store.categorical_weights:
            if key != OTHER_BUCKET:
                assert hashed[key] >= k
    hist = store.categorical_histogram
    if hist:
        for b in hist["bins"]:
            assert b == 0 or count_lower_bound(b, n) >= k

    stamp = table.columns["stamp"]
    when = [datetime(2021, 1 + r[2], 11 + r[2], r[3], 30) for r in rows]
    for hist_name, key in (
        ("hour_histogram", lambda d: d.hour),
        ("dow_histogram", lambda d: d.weekday()),
    ):
        bins = getattr(stamp, hist_name)
        if bins:
            for i, b in enumerate(bins):
                if b:
                    assert sum(1 for d in when if key(d) == i) >= k, (hist_name, i, k)
    temporal = stamp.temporal_histogram or {}
    for i, b in enumerate(temporal.get("month_weights", [])):
        if b:
            assert sum(1 for d in when if d.month - 1 == i) >= k


def test_manifest_counts_withheld_cells(tmp_path):
    rows = [("red", 100, 0, 3)] * 60 + [("blue", 101, 1, 4)] * 3 + [("rose", 102, 2, 5)] * 3
    path = tmp_path / "t.csv"
    _write(path, rows)
    safe = to_safe_profile(
        shape.profile(str(path)), SafeConfig(columns={"label": ColumnConfig(k=5)})
    )
    manifest = safe.redaction_manifest["tables"]["t"]
    assert manifest["label"]["categories_dropped"] == 2
    assert manifest["label"]["cells_suppressed"] >= 2
    assert manifest["stamp"]["cells_suppressed"] >= 1  # hours with 2 rows are withheld


# ---- the release surfaces -------------------------------------------------------------------


def _shape(**col):
    return {"rows": 200, "columns": {"x": {"kind": "text", "count": 200, **col}}}


@given(
    st.dictionaries(st.text("abcdef", min_size=1, max_size=3), st.integers(1, 80), min_size=1),
    st.lists(st.integers(0, 60), min_size=1, max_size=12),
    st.integers(2, 10),
)
@settings(max_examples=150, deadline=None)
def test_release_surfaces_never_release_a_cell_below_the_minimum(counts, hist, k):
    topk = [[v, c] for v, c in counts.items()]
    s = _shape(topk=topk, value_counts_ext=dict(counts), histogram=list(hist))
    for out in (
        suppress_shape(s, SuppressionPolicy(min_count=1, suppress_topk_below=k)),
        release_for(s, {}, "PUBLIC", minimum_cohort=k).shape,
    ):
        col = out["columns"]["x"]
        assert all(c >= k for _, c in col["topk"])
        assert all(v >= k for v in col.get("value_counts_ext", {}).values())
        assert all(b == 0 or b >= k for b in col.get("histogram", []))


def test_release_for_applies_cell_suppression_with_its_minimum_cohort():
    s = _shape(
        topk=[["a", 90], ["b", 3]],
        value_counts_ext={"a": 90, "b": 3, "c": 2},
        enum_values={"a": 0.9, "b": 0.015, "c": 0.01, "d": 0.075},
        hour_histogram=[0.5, 0.49, 0.01],
    )
    r = release_for(s, {}, "PUBLIC", minimum_cohort=5)
    col = r.shape["columns"]["x"]
    assert col["topk"] == [["a", 90]]
    assert col["value_counts_ext"] == {"a": 90, OTHER_BUCKET: 5}
    assert set(col["enum_values"]) == {"a", "d", OTHER_BUCKET}
    assert col["hour_histogram"][2] == 0 and col["hour_histogram"][0] > 0.5
    assert r.allowed


def test_value_lists_without_counts_cannot_be_checked_and_are_removed():
    s = _shape(enum_values=["alice", "bob"], histogram=["x", "y"], temporal_histogram="odd")
    r = release_for(s, {}, "PUBLIC")
    col = r.shape["columns"]["x"]
    assert not {"enum_values", "histogram", "temporal_histogram"} & set(col)
    assert {"columns.x.enum_values", "columns.x.histogram"} <= set(r.removed)


def test_column_cells_returns_a_copy_and_counts_what_it_withheld():
    col = {"topk": [["a", 9], ["b", 1]], "value_counts_ext": {"a": 9, "b": 1}}
    before = copy.deepcopy(col)
    out, n, removed = suppress_column_cells(col, 5, 10)
    assert col == before and n == 3 and removed == []
    assert out["topk"] == [["a", 9]]
    # b (1 row) is folded; __OTHER__ alone would be 1 < 5, so a joins it: 10 rows in one cell.
    assert out["value_counts_ext"] == {OTHER_BUCKET: 10}
