from shape.quality import evaluate


def test_non_numeric_observed_value_fails_the_range_rule_instead_of_raising():
    report = evaluate({"rows": "10"}, {"rows": {"min": 5}})
    assert not report.passed
    assert report.results[0].passed is False
    assert report.results[0].observed == "10"


def test_numeric_range_still_passes_and_fails():
    assert evaluate({"rows": 10}, {"rows": {"min": 5, "max": 20}}).passed
    assert not evaluate({"rows": 30}, {"rows": {"min": 5, "max": 20}}).passed
