from shape.capture import capture_rows
from shape.quality import infer_rules


def test_infer_rules():
    s = capture_rows([{"id": 1, "x": 2}, {"id": 2, "x": 3}]).to_dict()
    r = infer_rules(s)
    assert any(x.field == "id" and x.kind == "not_null" for x in r)


def test_infer_rules_read_a_v2_model_and_trust_only_what_the_evidence_supports():
    def col(name, **kw):
        return {
            "name": name,
            "arrow_type": "x",
            "kind": "int",
            "count": 1000,
            "null_count": 0,
            "error_models": {},
            **kw,
        }

    err = {"cardinality": {"algorithm": "hyperloglog", "exact": False, "relative_error": 0.01}}
    model = {
        "schema_version": 2,
        "engine": "t",
        "mode": "bounded",
        "tables": {
            "t": {
                "name": "t",
                "rows": 1000,
                "columns": [
                    col("exact_unique", distinct=1000.0, distinct_exact=True, min=1, max=9),
                    col("exact_dupes", distinct=999.0, distinct_exact=True),
                    col("sketch_unique", distinct=996.0, distinct_exact=False, error_models=err),
                    col("sketch_dupes", distinct=500.0, distinct_exact=False, error_models=err),
                    col("has_nulls", null_count=5, distinct=995.0, distinct_exact=True),
                ],
            }
        },
    }
    rules = {(r.field, r.kind) for r in infer_rules(model)}
    assert ("exact_unique", "unique") in rules and ("sketch_unique", "unique") in rules
    assert ("exact_dupes", "unique") not in rules and ("sketch_dupes", "unique") not in rules
    assert ("has_nulls", "unique") in rules  # 995 distinct among 995 non-null rows
    assert ("has_nulls", "not_null") not in rules
    assert ("exact_unique", "min") in rules and ("exact_unique", "max") in rules
