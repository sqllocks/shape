"""W1-06: the schema checker understands the keywords the published generation spec schema uses
(``allOf``, ``if``/``then``, ``not``, ``patternProperties``, ``minItems``) and reports where."""

from __future__ import annotations

from shape import schemacheck


def test_all_of_if_then() -> None:
    schema = {
        "type": "object",
        "allOf": [
            {
                "if": {"properties": {"kind": {"const": "a"}}, "required": ["kind"]},
                "then": {"required": ["x"]},
            }
        ],
    }
    assert schemacheck.validate({"kind": "a"}, schema) == ["$: missing required key 'x'"]
    assert schemacheck.validate({"kind": "a", "x": 1}, schema) == []
    assert schemacheck.validate({"kind": "b"}, schema) == []
    assert schemacheck.validate({}, schema) == []


def test_not_pattern_properties_and_min_items() -> None:
    schema = {
        "type": "object",
        "patternProperties": {"^x-": {}},
        "additionalProperties": False,
        "properties": {"a": {"type": "array", "minItems": 1}},
    }
    assert schemacheck.validate({"x-note": [1], "a": [1]}, schema) == []
    assert schemacheck.validate({"y": 1}, schema) == ["$: unexpected key 'y'"]
    assert schemacheck.validate({"a": []}, schema) == ["$.a: 0 items, fewer than minItems 1"]
    negated = {"not": {"required": ["k"]}}
    assert schemacheck.validate({}, negated) == []
    assert schemacheck.validate({"k": 1}, negated) != []


def test_problems_carry_the_path_as_parts() -> None:
    schema = {"properties": {"a": {"items": {"type": "integer"}}}}
    found = schemacheck.problems({"a": [1, "x"]}, schema)
    assert [(p.path, p.message) for p in found] == [(("a", 1), "expected ['integer'], got str")]
    assert schemacheck.validate({"a": [1, "x"]}, schema) == [
        "$.a[1]: expected ['integer'], got str"
    ]


def test_maximum() -> None:
    assert schemacheck.validate(2, {"type": "number", "maximum": 1}) == ["$: 2 > maximum 1"]
    assert schemacheck.validate(1, {"type": "number", "maximum": 1}) == []


def test_an_unexpected_key_names_the_key() -> None:
    found = schemacheck.problems(
        {"a": {"b": 1}}, {"properties": {"a": {"additionalProperties": False}}}
    )
    assert [(p.path, p.key) for p in found] == [(("a",), "b")]
