"""Regression tests for quality.policy, quality.core and the verify configuration (AUD-quality)."""

from __future__ import annotations

import pytest

from shape.quality import Rule, VerifyConfig, VerifyConfigError, evaluate, validate_rows


# #475: a value of the wrong type crashed validate_rows; a bad rule passed on empty input
@pytest.mark.parametrize(
    ("rule", "value"),
    [
        (Rule("a", "min", 0), "x"),
        (Rule("a", "max", 10), {"k": 1}),
        (Rule("a", "in", (1, 2)), [1]),
    ],
)
def test_a_value_that_cannot_be_compared_violates_the_rule(rule, value):
    result = validate_rows([{"a": value}], (rule,))
    assert not result.passed
    assert [(v.row, v.rule, v.observed) for v in result.violations] == [(0, rule.kind, value)]


def test_unhashable_values_are_compared_for_uniqueness():
    result = validate_rows([{"a": [1]}, {"a": [1]}, {"a": [2]}], (Rule("a", "unique"),))
    assert [(v.row, v.rule) for v in result.violations] == [(1, "unique")]


def test_an_unknown_rule_is_refused_before_any_row_is_read():
    with pytest.raises(ValueError, match="bogus"):
        validate_rows([], (Rule("a", "bogus"),))


def test_a_unique_rule_on_an_iterator_is_refused_before_it_is_consumed():
    rows = iter([{"a": 1}])
    with pytest.raises(ValueError, match="materialized"):
        validate_rows(rows, (Rule("a", "unique"),))
    assert next(rows) == {"a": 1}


def test_the_rules_still_find_what_they_did():
    rows = [{"a": 1, "b": None}, {"a": 1, "b": 5}, {"a": 11, "b": 3}]
    rules = (Rule("a", "unique"), Rule("a", "max", 10), Rule("b", "not_null"), Rule("b", "in", {3}))
    found = sorted((v.row, v.rule) for v in validate_rows(rows, rules).violations)
    assert found == [(0, "not_null"), (1, "in"), (1, "unique"), (2, "max")]


# #476: evaluate crashed on a value that cannot be compared with min or max
def test_evaluate_fails_a_rule_whose_value_cannot_be_compared():
    report = evaluate({"x": "a", "y": 5}, {"x": {"min": 0}, "y": {"min": 0, "max": 9}})
    assert [(r.rule, r.passed) for r in report.results] == [("x", False), ("y", True)]
    assert not report.passed


BASE = {"format": "shape-verify-config", "version": 1}


# #474: NaN bounds, min above max and start after end were accepted
@pytest.mark.parametrize(
    "extra",
    [
        {"ranges": {"t.x": {"min": float("nan")}}},
        {"ranges": {"t.x": {"min": 5, "max": 1}}},
        {"date_range": {"start": "2021-01-01", "end": "2020-01-01"}},
        {"date_range": {"start": "2021-01-01T00:00:00+00:00", "end": "2021-01-01T00:00:00+05:00"}},
    ],
)
def test_an_impossible_verify_configuration_is_refused(extra):
    with pytest.raises(VerifyConfigError):
        VerifyConfig.from_dict({**BASE, **extra})


def test_a_sensible_verify_configuration_still_loads():
    cfg = VerifyConfig.from_dict(
        {
            **BASE,
            "ranges": {"t.x": {"min": 1, "max": 1}},
            "date_range": {"start": "2020-01-01", "end": "2020-01-01T23:59:59"},
        }
    )
    assert cfg.rules["ranges"]["t.x"] == {"min": 1, "max": 1}
