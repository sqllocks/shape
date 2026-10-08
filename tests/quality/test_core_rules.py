"""``shape.quality.evaluate``: range, equality and severity rules on a summary (AUD-tests)."""

from shape.quality import QualityReport, RuleResult, evaluate


def test_a_range_rule_checks_min_and_max_inclusively():
    rules = {"rows": {"min": 10, "max": 20}}
    assert evaluate({"rows": 10}, rules).passed
    assert evaluate({"rows": 20}, rules).passed
    assert not evaluate({"rows": 9}, rules).passed
    assert not evaluate({"rows": 21}, rules).passed


def test_a_missing_value_fails_a_range_rule():
    report = evaluate({}, {"rows": {"min": 1}})
    assert report.results == (RuleResult("rows", False, None, {"min": 1}, "error"),)
    assert not report.passed


def test_a_plain_value_is_an_equality_rule():
    assert evaluate({"mode": "exact"}, {"mode": "exact"}).passed
    assert not evaluate({"mode": "bounded"}, {"mode": "exact"}).passed
    assert not evaluate({}, {"mode": "exact"}).passed


def test_a_failed_warning_does_not_fail_the_report():
    report = evaluate({"nulls": 0.2}, {"nulls": {"max": 0.1, "severity": "warning"}})
    (result,) = report.results
    assert result == RuleResult("nulls", False, 0.2, {"max": 0.1}, "warning")
    assert report.passed


def test_one_failed_error_fails_the_report_and_every_rule_is_reported():
    report = evaluate(
        {"rows": 5, "nulls": 0.0},
        {"rows": {"min": 10}, "nulls": {"max": 0.1}},
    )
    assert [(r.rule, r.passed) for r in report.results] == [("rows", False), ("nulls", True)]
    assert not report.passed


def test_no_rules_pass():
    assert evaluate({"rows": 1}, {}) == QualityReport(())
    assert QualityReport(()).passed
