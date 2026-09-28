import pytest

from shape.query import ShapeQueryError, query

S = {
    "rows": 10,
    "columns": {"email": {"kind": "text", "null_count": 2, "classification": "PII"}},
    "relationships": {"correlations": [{"source": "a", "target": "b", "rho": 0.8}]},
}


def test_queries():
    assert query(S, "rows") == 10
    assert query(S, 'column("email").null_count') == 2
    assert query(S, 'classification("email")') == "PII"
    assert query(S, 'relationship("a","b").rho') == 0.8


def test_query_is_not_eval():
    with pytest.raises(ShapeQueryError):
        query(S, '__import__("os").system("echo pwn")')
