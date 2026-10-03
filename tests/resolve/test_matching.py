"""W3-09 (#72): pairwise matching with string distances and numeric/date tolerances."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pyarrow as pa
import pytest

from shape.resolve.matching import FieldMatch, score_pairs


def table() -> pa.Table:
    return pa.table(
        {
            "name": ["john smith", "jon smith", "mary jones", None, "john smith"],
            "amount": [100.0, 100.5, 200.0, 100.0, None],
            "born": [
                dt.date(1990, 1, 1),
                dt.date(1990, 1, 3),
                dt.date(1985, 5, 5),
                None,
                dt.date(1990, 1, 1),
            ],
            "grade": ["A", "A", "B", "A", "A"],
        }
    )


def test_text_score_orders_similar_above_dissimilar() -> None:
    pairs = np.array([[0, 1], [0, 2]])
    s = score_pairs(table(), pairs, [FieldMatch("name", "jaro_winkler")]).scores
    assert s[0] > 0.9 and s[1] < 0.6


def test_numeric_tolerance_is_linear_and_absolute_or_relative() -> None:
    pairs = np.array([[0, 1], [0, 2]])
    absolute = score_pairs(table(), pairs, [FieldMatch("amount", "numeric", tolerance=1.0)]).scores
    assert absolute.tolist() == pytest.approx([0.5, 0.0])
    rel = score_pairs(
        table(), pairs, [FieldMatch("amount", "numeric", tolerance=0.01, relative=True)]
    ).scores
    assert rel.tolist() == pytest.approx([1 - (0.5 / 100.5) / 0.01, 0.0])
    zero = score_pairs(table(), np.array([[0, 3]]), [FieldMatch("amount", "numeric")]).scores
    assert zero.tolist() == [1.0]


def test_date_tolerance_in_days_boundaries() -> None:
    pairs = np.array([[0, 1]])
    for days, want in ((2, 0.0), (4, 0.5), (1, 0.0)):
        got = score_pairs(table(), pairs, [FieldMatch("born", "date", tolerance=days)]).scores[0]
        assert got == pytest.approx(want)
    assert (
        score_pairs(table(), np.array([[0, 4]]), [FieldMatch("born", "date", tolerance=1)]).scores[
            0
        ]
        == 1.0
    )


def test_missing_values_drop_out_of_the_weighted_mean() -> None:
    fields = [FieldMatch("name", "exact", weight=1.0), FieldMatch("grade", "exact", weight=1.0)]
    r = score_pairs(table(), np.array([[0, 3], [0, 4]]), fields)
    assert r.scores.tolist() == [1.0, 1.0]  # row 3 has no name: only grade counts
    allnull = score_pairs(table(), np.array([[3, 3]]), [FieldMatch("name", "exact")])
    assert allnull.scores.tolist() == [0.0]


def test_weights_and_per_field_similarities() -> None:
    fields = [FieldMatch("name", "exact", weight=3.0), FieldMatch("grade", "exact", weight=1.0)]
    r = score_pairs(table(), np.array([[0, 1]]), fields)
    assert r.scores.tolist() == pytest.approx([0.25])
    assert r.similarities.shape == (1, 2) and r.similarities.tolist() == [[0.0, 1.0]]


def test_phonetic_and_exact_kinds() -> None:
    t = pa.table({"n": ["Robert", "Rupert", "Ann"]})
    s = score_pairs(t, np.array([[0, 1], [0, 2]]), [FieldMatch("n", "phonetic")]).scores
    assert s.tolist() == [1.0, 0.0]


def test_validation() -> None:
    with pytest.raises(ValueError, match="unknown match kind"):
        FieldMatch("name", "bogus")
    with pytest.raises(ValueError, match="weight"):
        FieldMatch("name", "exact", weight=0)
    with pytest.raises(ValueError, match="tolerance"):
        FieldMatch("amount", "numeric", tolerance=-1)
    with pytest.raises(ValueError, match="no column"):
        score_pairs(table(), np.array([[0, 1]]), [FieldMatch("zzz", "exact")])
    with pytest.raises(ValueError, match="numeric"):
        score_pairs(table(), np.array([[0, 1]]), [FieldMatch("name", "numeric")])
    with pytest.raises(ValueError, match="at least one"):
        score_pairs(table(), np.array([[0, 1]]), [])
    assert score_pairs(
        table(), np.empty((0, 2), dtype=np.int64), [FieldMatch("name", "exact")]
    ).scores.shape == (0,)
