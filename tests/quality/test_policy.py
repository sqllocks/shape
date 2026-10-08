from shape.quality.policy import Rule, validate_rows


def test_quality():
    rows = [{"id": 1, "age": 2}, {"id": 1, "age": -1}]
    r = validate_rows(rows, (Rule("id", "unique"), Rule("age", "min", 0)))
    assert not r.passed and len(r.violations) == 2
