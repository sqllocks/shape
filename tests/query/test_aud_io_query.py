"""AUD-io: regression tests for shape.query (issues filed by the io audit)."""

from __future__ import annotations

import pytest

from shape.query import ShapeQueryError, query


def _model():
    return {
        "schema_version": 2,
        "engine": "t",
        "mode": "exact",
        "relationships": [],
        "tables": {
            "t": {
                "name": "t",
                "rows": 5,
                "columns": [
                    {
                        "name": "c",
                        "arrow_type": "int64",
                        "kind": "int",
                        "count": 5,
                        "null_count": 1,
                        "error_models": {},
                    },
                    {
                        "name": "a.b",
                        "arrow_type": "int64",
                        "kind": "int",
                        "count": 5,
                        "null_count": 2,
                        "error_models": {},
                    },
                ],
            }
        },
    }


def test_504_a_qualified_column_name_works_on_a_one_table_model():
    m = _model()
    assert query(m, 'column("t.c").null_count') == 1
    assert query(m, 'column("c").null_count') == 1
    assert query(m, 'column("a.b").null_count') == 2  # a dotted column name is still found
    assert query(m, 'column("u.c")') is None


def test_504_a_path_after_a_missing_name_says_what_is_missing():
    m = _model()
    assert query(m, 'column("zz")') is None
    with pytest.raises(ShapeQueryError, match=r'column\("zz"\) was not found'):
        query(m, 'column("zz").null_count')
    with pytest.raises(ShapeQueryError, match=r'relationship\("x", "y"\) was not found'):
        query(m, 'relationship("x","y").rho')
    with pytest.raises(ShapeQueryError, match="cannot access x"):
        query(m, "rows.x")
