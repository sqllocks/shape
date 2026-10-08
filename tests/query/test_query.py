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


# ---- P1-10: the query reads the v2 model; P21 relationships match exactly


def _model_with_relationships():
    return {
        "schema_version": 2,
        "engine": "t",
        "mode": "exact",
        "tables": {"t": {"name": "t", "rows": 5, "columns": []}},
        "relationships": [
            {"kind": "correlation", "source": "x", "target": "y", "rho": 0.5},
            {"kind": "foreign_key", "source": "order_id", "target": "id"},
            {"kind": "correlation", "source": "x", "target": "z", "rho": 0.1},
        ],
    }


def test_relationship_matches_exactly():
    m = _model_with_relationships()
    assert query(m, 'relationship("x","y").rho') == 0.5
    assert query(m, 'relationship("y","x").rho') == 0.5  # correlations read both ways
    assert query(m, 'relationship("order_id","id").kind') == "foreign_key"
    assert query(m, 'relationship("id","order_id")') is None  # a foreign key has a direction


def test_relationship_with_the_same_name_twice_does_not_match_anything_that_mentions_it():
    m = _model_with_relationships()
    assert query(m, 'relationship("x","x")') is None
    assert query(m, 'relationship("y","y")') is None
    m["relationships"].append({"kind": "self", "source": "x", "target": "x", "n": 1})
    assert query(m, 'relationship("x","x").n') == 1


def test_v1_relationship_groups_migrate_to_entries_with_a_kind():
    s = {
        "rows": 1,
        "columns": {},
        "relationships": {"correlations": [{"source": "a", "target": "b"}]},
    }
    assert query(s, 'relationship("a","b").kind') == "correlations"


def test_rows_and_columns_of_a_multi_table_model():
    import pytest

    m = _model_with_relationships()
    m["tables"]["u"] = {
        "name": "u",
        "rows": 9,
        "columns": [
            {
                "name": "c",
                "arrow_type": "x",
                "kind": "int",
                "count": 9,
                "null_count": 2,
                "error_models": {},
            }
        ],
    }
    assert query(m, 'rows("u")') == 9 and query(m, 'rows("t")') == 5
    assert query(m, 'column("u.c").null_count') == 2
    with pytest.raises(ShapeQueryError):
        query(m, "rows")
