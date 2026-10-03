"""AUD-gen: ``shape fidelity`` on a column with no values (#178)."""

from __future__ import annotations

import pyarrow as pa  # type: ignore[import-untyped]

from shape.generation.report.compare import compare_column, compare_tables


def test_an_all_empty_column_is_scored_like_any_all_null_column():
    # 178: a CSV column with no values reads as the Arrow null type, and scoring it raised
    # ArrowNotImplementedError: Function 'count_distinct' has no kernel matching input types
    # (null). It now scores as an all-null text column does (the T-21 comparator's marks).
    empty = pa.nulls(100)
    text = pa.array([None] * 100, pa.string())
    assert compare_column("c", empty, empty).score == compare_column("c", text, text).score
    report = compare_tables({"t": pa.table({"e": empty})}, {"t": pa.table({"e": empty})})
    assert report.tables[0].columns[0].cardinality_ratio == 0.0
