"""W5-04: ``shape.schemacheck`` learned ``maximum``, ``pattern`` and ``not``, which the emitted
JSON Schema needs for a row that breaks a range or a pattern to be rejected."""

from __future__ import annotations

from shape import schemacheck


def test_maximum() -> None:
    s = {"type": "number", "maximum": 5}
    assert schemacheck.validate(5, s) == []
    assert schemacheck.validate(5.5, s) and schemacheck.validate(6, s)
    assert schemacheck.validate("9", {"maximum": 5}) == []  # not a number: not this keyword's


def test_pattern() -> None:
    s = {"type": "string", "pattern": r"^\d{3}$"}
    assert schemacheck.validate("123", s) == []
    assert schemacheck.validate("1234", s) and schemacheck.validate("abc", s)
    assert schemacheck.validate(5, {"pattern": "x"}) == []  # not a string: not this keyword's
    assert schemacheck.validate("ab", {"pattern": "b"}) == []  # a pattern is not anchored


def test_not() -> None:
    s = {"not": {"type": "null"}}
    assert schemacheck.validate(1, s) == [] and schemacheck.validate("", s) == []
    assert schemacheck.validate(None, s)


def test_the_new_keywords_inside_properties() -> None:
    s = {"type": "object", "properties": {"n": {"type": ["integer", "null"], "maximum": 3}}}
    assert schemacheck.validate({"n": None}, s) == []
    assert schemacheck.validate({"n": 4}, s) == ["$.n: 4 > maximum 3"]
